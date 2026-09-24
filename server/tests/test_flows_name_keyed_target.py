"""A TARGET with a name and no typed coordinates is keyed on its NAME (#189 A5,
spec 3.3).

THE FAILURE. S1 keyed a single TARGET on the geometry it compiled to. For a
TARGET whose coordinates were typed into the node that is the field the
operator drew. For one that carries only a NAME, the coordinates are the
catalogue's answer at the compile's ``when`` (``to_plan._coords`` ->
``catalog_coords(name, when)``), and for a planet, a comet or the Moon that
answer moves by the hour (a deep-sky name resolves to a fixed J2000 row, which
does not). So the ids moved with it, night two named steps night one never
banked on, and the campaign restarted every time it was compiled - the exact
fault S1's ids exist to remove, for the one kind of TARGET whose field
genuinely moves.

So such a TARGET is keyed on its name, through ``identity.target_key``, the ONE
function that decides a single TARGET's key from its compiled entry; both
``to_plan._identify`` (which mints the id) and ``progress._single`` (which
finds it again) call it. The angle and the grid stay in the key, and the key
is namespaced so it can never equal a geometry key.

The catalogue is replaced by ``_moving``, whose answer moves with ``when``, so
the property is tested against the thing that actually moves rather than
against whatever the shipped catalogue does for one name today.

Every test names the mutation it guards and quotes the failure it produced,
run from a byte backup of the file mutated and restored byte-identical after.
"""
from __future__ import annotations

import re

import pytest

import astrodeck.flows.to_plan as to_plan_module
from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.models import plan_identity_errors
from astrodeck.sequence.session import Session, SessionFrame

FLOW = "flow-name-keyed"
#: Two compile times a day apart. Neither is anything but a number here.
DAY_ONE = 1_790_000_000.0
DAY_TWO = DAY_ONE + 86_400.0


def _moving(name, when=None):
    """A catalogue whose answer moves with ``when``: 0.1 h of RA and half a
    degree of Dec a day, about what Mars does. Every name resolves, because
    WHERE a name resolves is not what these tests are about."""
    days = ((DAY_ONE if when is None else when) - DAY_ONE) / 86_400.0
    return (10.0 + 0.1 * days) % 24.0, 12.0 - 0.5 * days


@pytest.fixture(autouse=True)
def moving_catalogue(monkeypatch):
    monkeypatch.setattr(to_plan_module, "catalog_coords", _moving)


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph(*, name="Mars", ra="", dec="", rotation=-1):
    """dusk -> TARGET -> CAPTURE Ha -> FILTER CYCLE L/R: one target, three
    steps from two stages. ``ra`` and ``dec`` are written as "" rather than
    left out, because a missing param is filled with the palette default (M31,
    #190) and that would be a typed coordinate."""
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name=name, ra=ra, dec=dec,
                  rotation=rotation),
               _n("c", "capture", x=200, filter="Ha", exposure=120,
                  gain=100, bin="1", count=10, goal=0),
               _n("y", "cycle", x=300, plan="L 60, R 60", cycles=3,
                  perCycle=1, gain=100, bin="1")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
               _e("c", "complete", "y", "run")])


def _compile(graph, when):
    compiled = compile_plan(graph, "n")
    plan, _unmapped = to_sequence_plan(compiled, graph, flow_id=FLOW,
                                       when=when)
    return compiled, plan


def _ids(plan):
    return [(t.id, [s.id for s in t.steps]) for t in plan.targets]


def _all_ids(plan):
    return {i for tid, sids in _ids(plan) for i in (tid, *sids)}


class TestKeyedOnTheName:
    def test_two_compiles_a_day_apart_give_the_same_ids(self):
        """THE FIX. The catalogue's answer moved 0.1 h and half a degree
        between the two compiles, and every target and step id stayed put.

        Mutant "key on the resolved coordinates" (``target_key`` returns the
        geometry key for every entry, which is S1's rule) failed:
            AssertionError: assert [('e8a6021df8...d8409ddf96'])] ==
            [('f2f96e27c5...266da50c1a'])]
              At index 0 diff: ('e8a6021df841572dbee13fa1083471ce',
              ['36e0f3a6a15c58449af2dee1f2aa2340', ...]) !=
              ('f2f96e27c5ba5fb2a307f0af9b3adb9d',
              ['293ff929c4d55002bad2f10901eabec4', ...])
        Mutant "_identify keys on geometry" (``to_plan`` calls
        ``identity.geometry_key`` itself instead of ``target_key``) failed
        with the same diff.
        """
        _c1, one = _compile(_graph(), DAY_ONE)
        _c2, two = _compile(_graph(), DAY_TWO)
        assert (one.targets[0].ra_hours, one.targets[0].dec_deg) != \
            (two.targets[0].ra_hours, two.targets[0].dec_deg), \
            "premise: the resolved coordinates moved between the compiles"
        assert len(one.targets[0].steps) == 3, "premise: capture + 2 slots"
        assert _ids(one) == _ids(two)
        assert plan_identity_errors(one) == []

    def test_the_id_is_the_name_key_recipe(self):
        """End to end: the TARGET's node id and its name key make the target
        id, exactly as ``identity`` spells it; the step ids hang off it as
        they do for any target.

        Mutant "key on the resolved coordinates" failed here too:
            AssertionError: assert 'e8a6021df841...13fa1083471ce' ==
            '15669a418363...ef72f347b9752'
        """
        _c, plan = _compile(_graph(), DAY_ONE)
        key = identity.name_key("Mars", None)
        assert plan.targets[0].id == identity.target_id(
            identity.group_id(FLOW, "t", key), 0, 0)

    def test_a_name_with_one_typed_coordinate_is_keyed_on_the_name(self):
        """``to_plan`` falls back to the name unless BOTH coordinates are
        typed, so an RA with no Dec resolves by name. The key must read the
        entry the same way, or it keys a moving answer on its geometry. The
        rule lives in one place (``identity.typed_coordinates``) and
        ``_coords`` reads it too.

        Mutant "typed means either coordinate" (``typed_coordinates`` returns
        ``bool(ra or dec)``) failed, the half-typed entry now read as typed
        and dropped for its unparseable Dec:
            astrodeck.flows.to_plan.GraphNotRunnable: this flow has no target
            the run could point at - add a TARGET node with coordinates, or a
            POOL whose members are catalogue names
        Mutant "key on the resolved coordinates" failed here too, the ids
        moving with the answer (the same diff as the first test above).
        """
        g = _graph(ra="05h 35m 17s", dec="")
        _c1, one = _compile(g, DAY_ONE)
        _c2, two = _compile(g, DAY_TWO)
        assert one.targets[0].ra_hours != two.targets[0].ra_hours, \
            "premise: the name was resolved, and the answer moved"
        assert _ids(one) == _ids(two)


class TestWhatStillReKeys:
    """What must still change the id. Each would pass under a fix that keyed
    too little."""

    def test_control_a_typed_target_is_keyed_on_its_geometry(self):
        """A TARGET whose coordinates were typed keys on them exactly as S1
        did: moving it re-keys (D5), and a second compile at another time
        does not, because nothing about typed coordinates moves.

        Mutant "a name always wins" (``target_key`` keys every named entry on
        its name, typed or not) failed, the moved target keeping its ids:
            AssertionError: a moved field kept an id
            assert {'1b1ad1a1555...229c4e205ffc'} == set()
              Extra items in the left set:
              '1b1ad1a1555c5b48b116506d7454952a'
              ...
        and so did S1's own guards in test_flows_identity.py
        (``test_moving_the_target_gives_new_ids`` for RA and Dec,
        ``test_the_ids_are_the_spec_recipe_over_the_compiled_nodes``) and the
        premise of ``test_exact_banked_and_owed_for_frames_on_two_steps``.
        """
        here = _graph(name="M42", ra="05h 35m 17s", dec="-05 23 28")
        moved = _graph(name="M42", ra="05h 45m 17s", dec="-05 23 28")
        _c, a = _compile(here, DAY_ONE)
        _c, b = _compile(moved, DAY_ONE)
        _c, again = _compile(here, DAY_TWO)
        assert _all_ids(a) & _all_ids(b) == set(), "a moved field kept an id"
        assert _ids(again) == _ids(a)
        key = identity.geometry_key(a.targets[0].ra_hours,
                                    a.targets[0].dec_deg, None)
        assert a.targets[0].id == identity.target_id(
            identity.group_id(FLOW, "t", key), 0, 0)

    def test_control_the_angle_is_still_in_the_key(self):
        """Spec 3.3 keys the angle: setting one re-frames the field, so its
        counts restart. That holds for a name-keyed TARGET too. -1 and -5 are
        both "any angle" and key as one.

        Mutant "angle left out of the name key" (``canonical_name`` writes
        ``rotation_deg`` as null whatever it is given) failed:
            AssertionError: setting an angle kept an id
            assert {'15669a41836...5029aba06a92'} == set()
              Extra items in the left set:
              'ae69a8bea63853e197677d9df3f12ffd'
              ...
        """
        _c, anyk = _compile(_graph(rotation=-1), DAY_ONE)
        _c, also = _compile(_graph(rotation=-5), DAY_TWO)
        _c, set30 = _compile(_graph(rotation=30), DAY_ONE)
        assert _ids(also) == _ids(anyk)
        assert _all_ids(set30) & _all_ids(anyk) == set(), \
            "setting an angle kept an id"

    def test_control_the_grid_is_still_in_the_key(self):
        """S1 has no grid on a TARGET, but S3 keys one the way it keys a
        geometry, so the grid fields are in the name key already.

        Mutant "grid left out of the name key" (``rows`` and ``cols`` never
        written by ``canonical_name``) failed:
            AssertionError: assert 'name:ceaba5e3578795e7' !=
            'name:ceaba5e3578795e7'
             +  where 'name:ceaba5e3578795e7' = <function name_key at
             0x000001C569FE6F20>('Mars', None, rows=2, cols=3)
        """
        single = identity.name_key("Mars", None)
        assert identity.name_key("Mars", None, rows=1, cols=1) == single
        assert identity.name_key("Mars", None, rows=2, cols=3) != single
        assert identity.name_key("Mars", None, overlap=0.15) != single

    def test_control_a_renamed_target_re_keys(self):
        """The name IS the field now, so a different name is a different
        field. Surrounding whitespace is not a different name: ``to_plan``
        strips it before naming the target, and the key reads the same name.

        Mutant "name left out of the name key" (``canonical_name`` writes
        ``""`` for every name) failed:
            AssertionError: assert {'0037f96332a...c728f0a1192b'} == set()
              Extra items in the left set:
              'e108e3dd60e4550ba7ab5322cce6e343'
              ...
        Mutant "unstripped name" (``target_key`` keys ``entry["name"]`` as
        given) failed the whitespace half:
            AssertionError: assert [('3646072a9f...266c8c5fd7'])] ==
            [('15669a4183...7f9e28d992'])]
        """
        _c, mars = _compile(_graph(name="Mars"), DAY_ONE)
        _c, jupiter = _compile(_graph(name="Jupiter"), DAY_ONE)
        _c, padded = _compile(_graph(name="  Mars "), DAY_TWO)
        assert _all_ids(mars) & _all_ids(jupiter) == set()
        assert _ids(padded) == _ids(mars)


class TestTheNamespace:
    def test_a_target_named_like_a_geometry_key_does_not_collide(
            self, monkeypatch):
        """A TARGET literally named after the geometry key of the same node's
        typed coordinates, and resolving to those very coordinates. The two
        are keyed on different things, so they share no id, and no name key
        is ever in the space geometry keys live in (16 lowercase hex).

        Mutant "no namespace" (``name_key`` returns the bare 16-hex digest)
        failed at the key check. The ids themselves still differ under it,
        because the name is hashed with the shape and the digest of a
        different text is a different digest: the namespace is what makes
        "never equal" true by construction rather than by improbability,
        the lesson of ``engine._focus_group_key``. The bare digest here is
        '091fc95796ba935c' against the geometry key '7aa8577027b1741c':
            AssertionError: assert False
             +  where False = <built-in method startswith of str object at
             0x000001A97DD61E70>('name:')
             +    where <built-in method startswith of str object at
             0x000001A97DD61E70> = '091fc95796ba935c'.startswith
        Mutant "key on the resolved coordinates" failed at the id check, the
        name-keyed TARGET taking the typed one's ids:
            AssertionError: assert {'19b37207156...6054aa999f9d'} == set()
              Extra items in the left set:
              '39721df4a7295e099b49e0df5e920770'
              ...
        """
        typed = _graph(name="M31", ra="00h 42m 44s", dec="+41 16 09")
        _c, a = _compile(typed, DAY_ONE)
        geo = identity.geometry_key(a.targets[0].ra_hours,
                                    a.targets[0].dec_deg, None)
        where = (a.targets[0].ra_hours, a.targets[0].dec_deg)

        def at_m31(name, when=None):
            return where

        monkeypatch.setattr(to_plan_module, "catalog_coords", at_m31)
        _c, b = _compile(_graph(name=geo), DAY_ONE)
        assert (b.targets[0].ra_hours, b.targets[0].dec_deg) == where, \
            "premise: the name resolved to the typed target's coordinates"
        key = identity.name_key(geo, None)
        assert key.startswith(identity.NAME_KEY_PREFIX)
        assert not re.fullmatch(r"[0-9a-f]{16}", key), \
            f"a name key must never look like a geometry key: {key!r}"
        assert _all_ids(a) & _all_ids(b) == set()


class TestProgressFindsTheBlock:
    def _session(self, plan, frames):
        return Session(id="session-1", status="dormant", nights=["n1"],
                       plan=plan, frames=frames, origin="flow",
                       origin_id=FLOW)

    def test_progress_reads_a_name_keyed_block_compiled_a_day_later(self):
        """Night one banked four Ha subs and one L. The card is read against
        a compile made a day later, when the catalogue's answer has moved: the
        block is found by the name key, and it banks all five.

        Mutant "progress keys on geometry" (``progress._single`` recomputes
        ``identity.geometry_key`` from each plan target, as S1 did) failed,
        the name-keyed block claiming no target and the plan refused as
        foreign:
            ValueError: 1 plan target(s) match no block of this compile
            (Mars): the plan was not compiled from it with
            flow_id='flow-name-keyed', so every count read against it would
            be wrong
        Mutant "_identify keys on geometry" (the other side of the same
        seam: ``to_plan`` stops calling ``target_key``) failed with the same
        refusal, which is why there is one function and not two copies.
        """
        _c1, night_one = _compile(_graph(), DAY_ONE)
        ha, lum, _red = night_one.targets[0].steps
        frames = ([SessionFrame(step_id=ha.id) for _ in range(4)]
                  + [SessionFrame(step_id=lum.id)])
        compiled, plan = _compile(_graph(), DAY_TWO)
        got = flow_progress(compiled, plan, self._session(night_one, frames),
                            flow_id=FLOW)
        block, = got["blocks"]
        panel, = block["panels"]
        assert panel["target_id"] == night_one.targets[0].id
        assert [(s["filter"], s["banked"]) for s in panel["steps"]] == [
            ("Ha", 4), ("L", 1), ("R", 0)]
        assert got["orphaned"] == {"frames": 0, "steps": 0}

    def test_control_progress_still_reads_a_typed_block(self):
        """The typed TARGET is found by its geometry, as before.

        Mutant "progress keys every block on its name" (``progress._single``
        calls ``identity.name_key`` for every entry) failed, and so did 19
        tests in test_flows_progress.py, every one with a typed TARGET:
            ValueError: 1 plan target(s) match no block of this compile
            (M42): the plan was not compiled from it with
            flow_id='flow-name-keyed', so every count read against it would
            be wrong
        """
        typed = _graph(name="M42", ra="05h 35m 17s", dec="-05 23 28")
        compiled, plan = _compile(typed, DAY_ONE)
        ha = plan.targets[0].steps[0]
        got = flow_progress(compiled, plan,
                            self._session(plan, [SessionFrame(step_id=ha.id)]),
                            flow_id=FLOW)
        assert got["blocks"][0]["panels"][0]["target_id"] == plan.targets[0].id
        assert got["blocks"][0]["banked"] == 1
