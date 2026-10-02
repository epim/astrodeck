# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A TARGET with a name and no typed coordinates is keyed on the catalogue's
CANONICAL IDENTITY for its name (#189 A5, #229, spec 3.3).

THE FAILURE A5 FIXED. S1 keyed a single TARGET on the geometry it compiled to.
For a TARGET whose coordinates were typed into the node that is the field the
operator drew. For one that carries only a NAME, the coordinates are the
catalogue's answer at the compile's ``when`` (``to_plan._coords`` ->
``tonight.resolve_target(name, when)``), and for a planet, a comet or the Moon
that answer moves by the hour (a deep-sky name resolves to a fixed J2000 row,
which does not). So the ids moved with it, night two named steps night one
never banked on, and the campaign restarted every time it was compiled.

THE FAILURE #229 FIXED. A5 keyed such a TARGET on the name AS TYPED. "M 31",
"M31" and "m31" are one object, one catalogue row, and they were three keys:
a spelling edit restarted a deep-sky campaign S1 would have kept. So the key
is the resolver's canonical identity instead: the catalogue id for a fixed row
("M31"), the canonical body name for a moving one ("Jupiter"). One resolver,
``tonight.resolve_target``, answers the coordinates, the identity and whether
the row moves; ``to_plan`` and ``progress._single`` both ask it, and
``identity.target_key`` (the ONE function that decides a single TARGET's key)
is handed the identity, so ``identity`` stays pure. The angle and the grid
stay in the key, and the key is namespaced so it can never equal a geometry
key.

Most tests replace the catalogue with ``_moving``, whose answer moves with
``when``, so the property is tested against the thing that actually moves
rather than against whatever the shipped catalogue does for one name today.
``TestTheCanonicalIdentity`` uses the shipped catalogue, because WHICH row a
spelling finds is exactly what it is about.

Every test names the mutation it guards and quotes the failure it produced,
run from a byte backup of the file mutated and restored byte-identical after.
"""
from __future__ import annotations

import re

import pytest

import astrodeck.flows.tonight as tonight_module
from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import NameResolution
from astrodeck.sequence.models import plan_identity_errors
from astrodeck.sequence.session import Session, SessionFrame

FLOW = "flow-name-keyed"
#: Two compile times a day apart. Neither is anything but a number here.
DAY_ONE = 1_790_000_000.0
DAY_TWO = DAY_ONE + 86_400.0

#: The shipped resolver, kept before the autouse fixture replaces it, for the
#: tests that are about which row a real spelling finds.
REAL_RESOLVER = tonight_module.resolve_target


def _moving(name, when=None):
    """A catalogue whose answer moves with ``when``: 0.1 h of RA and half a
    degree of Dec a day, about what Mars does. Every name resolves, because
    WHERE a name resolves is not what these tests are about. Its canonical
    identity is the name in title case, so "mars" and "Mars" are one body,
    as the shipped catalogue has it. It does NOT strip the name, so a test
    can see whether the caller did."""
    days = ((DAY_ONE if when is None else when) - DAY_ONE) / 86_400.0
    return NameResolution(ra_hours=(10.0 + 0.1 * days) % 24.0,
                          dec_deg=12.0 - 0.5 * days,
                          identity=str(name).title(), moves=True)


@pytest.fixture(autouse=True)
def moving_catalogue(monkeypatch):
    # On the module, where every caller looks it up at call time: this one
    # patch replaces the resolver for `to_plan` and `progress` alike.
    monkeypatch.setattr(tonight_module, "resolve_target", _moving)


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
        """End to end: the TARGET's node id and the name key of the
        CANONICAL identity make the target id, exactly as ``identity`` spells
        it; the step ids hang off it as they do for any target. The node
        says "mars" and the catalogue says "Mars", and the id is keyed on
        what the catalogue says (#229).

        Mutant "key on the resolved coordinates" failed here too:
            AssertionError: assert 'e8a6021df841...13fa1083471ce' ==
            '15669a418363...ef72f347b9752'
        Mutant "key the name as typed" (``target_key`` keys
        ``name_key(name, ...)``, the stripped name from the entry, as A5
        did) failed:
            AssertionError: assert '7c933c503755...bd4a31e5bc694' ==
            '15669a418363...ef72f347b9752'
        """
        _c, plan = _compile(_graph(name="mars"), DAY_ONE)
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
        """The object IS the field now, so a name that resolves to another
        object is a different field. Surrounding whitespace is not a
        different name: ``to_plan`` strips it before asking the catalogue
        (``_moving`` does not strip, so this sees whether ``to_plan`` did).

        Mutant "name left out of the name key" (``canonical_name`` writes
        ``""`` for every name) failed:
            AssertionError: assert {'0037f96332a...c728f0a1192b'} == set()
              Extra items in the left set:
              'e108e3dd60e4550ba7ab5322cce6e343'
              ...
        Mutant "unstripped name to the resolver" (``to_plan._coords`` asks
        ``resolve_target`` for ``entry["name"]`` as given) failed the
        whitespace half:
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
            return NameResolution(ra_hours=where[0], dec_deg=where[1],
                                  identity=str(name).strip(), moves=False)

        monkeypatch.setattr(tonight_module, "resolve_target", at_m31)
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

    def test_progress_reads_a_padded_name_as_to_plan_keyed_it(self):
        """The node says "  mars ", padding and all, and the compiled entry
        keeps it. ``to_plan`` and ``progress._single`` each trim the name
        before they ask the resolver (``_moving`` does not trim), so both ask
        about "mars", get "Mars", and find one block.

        Mutant "progress resolves the unstripped name" (``progress._single``
        asks ``resolve_target`` for ``entry["name"]`` as given) failed:
            ValueError: 1 plan target(s) match no block of this compile
            (mars): the plan was not compiled from it with
            flow_id='flow-name-keyed', so every count read against it would
            be wrong
        Mutant "unstripped name to the resolver" (the same in
        ``to_plan._coords``) failed with the same refusal from the other
        side of the seam.
        """
        compiled, plan = _compile(_graph(name="  mars "), DAY_ONE)
        assert compiled["targets"][0]["name"] == "  mars ", \
            "premise: the compiled entry keeps the padding"
        ha = plan.targets[0].steps[0]
        got = flow_progress(compiled, plan,
                            self._session(plan, [SessionFrame(step_id=ha.id)]),
                            flow_id=FLOW)
        assert got["blocks"][0]["panels"][0]["target_id"] == plan.targets[0].id
        assert got["blocks"][0]["banked"] == 1

    def test_a_name_the_catalogue_cannot_place_reads_as_a_dropped_block(
            self, monkeypatch):
        """A TARGET named "Nowhere", which the catalogue has no row for,
        beside one named "Mars". ``to_plan`` drops "Nowhere" (nothing to
        point at), so there is no identity to key it on, and
        ``progress._single`` has to answer None for its block BEFORE it asks
        ``target_key``, which refuses a name-only entry with no identity. The
        block owes nothing, and Mars keeps its frame.

        The branch is also taken when the resolver, a moment after the
        compile, cannot place a body the compile did place ("Mars could not
        be placed just now"). There the plan target is left unclaimed, and
        ``flow_progress`` refuses the plan as foreign: loud, not a wrong
        count. This test covers only the dropped entry.

        Mutant "progress raises on an unresolved name" (``progress._single``
        passes ``canonical=None`` for a name the resolver cannot place,
        instead of returning None) failed, a card the operator could not read
        because one of its TARGETs is unknown:
            ValueError: TARGET 'Nowhere' has no typed coordinates, so it is
            keyed on the catalogue's canonical identity for its name, and
            none was given: resolve the name (tonight.resolve_target) first
        (Before this test that mutant passed every owned test file: the one
        TARGET the other progress tests drop has no name, which never
        reaches the resolver.)
        """
        def knows_mars_only(name, when=None):
            if str(name).strip().lower() != "mars":
                return None
            return _moving(name, when)
        monkeypatch.setattr(tonight_module, "resolve_target", knows_mars_only)
        graph = FlowGraph(
            nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
                   _n("lost", "target", x=100, name="Nowhere", ra="",
                      dec="", rotation=-1),
                   _n("t", "target", x=100, y=100, name="Mars", ra="",
                      dec="", rotation=-1),
                   _n("c", "capture", x=200, filter="Ha", exposure=120,
                      gain=100, bin="1", count=10, goal=0)],
            edges=[_e("d", "window", "lost", "arm"),
                   _e("d", "window", "t", "arm"),
                   _e("t", "target", "c", "run")])
        compiled, plan = _compile(graph, DAY_ONE)
        assert [e["node_id"] for e in compiled["targets"]] == ["lost", "t"]
        assert [t.name for t in plan.targets] == ["Mars"], \
            "premise: to_plan dropped the name the catalogue cannot place"
        mars = plan.targets[0]
        got = flow_progress(compiled, plan,
                            self._session(plan,
                                          [SessionFrame(
                                              step_id=mars.steps[0].id)]),
                            flow_id=FLOW)
        lost, found = got["blocks"]
        assert (lost["node_id"], lost["panels"][0]["target_id"],
                lost["total"]) == ("lost", None, 0)
        assert (found["node_id"], found["panels"][0]["target_id"],
                found["banked"]) == ("t", mars.id, 1)

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


# ------------------------------------------------ the canonical identity (#229)

@pytest.fixture
def shipped_catalogue(monkeypatch):
    """Put the shipped resolver back for a test about which row a real
    spelling finds."""
    monkeypatch.setattr(tonight_module, "resolve_target", REAL_RESOLVER)


@pytest.mark.usefixtures("shipped_catalogue")
class TestTheCanonicalIdentity:
    """A name-only TARGET is keyed on the catalogue's canonical identity for
    the name: the catalogue id of a fixed row, the canonical body name of a
    moving one (#229). Against the shipped catalogue, because which row a
    spelling finds is the whole question."""

    def test_the_resolver_answers_identity_and_motion(self):
        """``tonight.resolve_target`` is the one resolver: four spellings of
        M31 find one fixed row whose id is "M31"; Jupiter in either case is
        one moving row whose id is its canonical name; a star is fixed; a
        name the catalogue does not carry is no answer.

        Mutant "identity is the name as typed" (``resolve_target`` returns
        ``identity=str(name).strip()``) failed:
            AssertionError: assert ['M 31', 'M31..., 'Andromeda'] ==
            ['M31', 'M31', 'M31', 'M31']
              At index 0 diff: 'M 31' != 'M31'
        Mutant "solar_system not a moving kind" (``MOVING_KINDS`` without
        ``"solar_system"``) failed:
            assert (False, False) == (True, True)
              At index 0 diff: False != True
        """
        spellings = ["M 31", "M31", "m31", "Andromeda"]
        hits = [tonight_module.resolve_target(s) for s in spellings]
        assert [h.identity for h in hits] == ["M31"] * 4
        assert not any(h.moves for h in hits)
        jup, jup_lower = (tonight_module.resolve_target("Jupiter"),
                          tonight_module.resolve_target("jupiter"))
        assert (jup.identity, jup_lower.identity) == ("Jupiter", "Jupiter")
        assert (jup.moves, jup_lower.moves) == (True, True)
        assert tonight_module.resolve_target("Vega").moves is False
        assert tonight_module.resolve_target("flow-name") is None

    def test_three_spellings_of_m31_are_one_campaign(self):
        """"M 31", "M31" and "m31" resolve to one catalogue row, so a TARGET
        renamed between them keeps its target id and every step id, and the
        id is the name key of "M31", the catalogue's id. Under A5 they were
        three keys and a spelling edit restarted the campaign.

        Mutant "key the name as typed" (``target_key`` keys
        ``name_key(name, ...)``, the stripped name from the entry, as A5
        did) failed:
            AssertionError: 'M 31' is keyed apart from 'M31'
            assert [('535bb08da1...94f49e83b3'])] ==
            [('e5c49f84a4...99a4872a8e'])]
              At index 0 diff: ('535bb08da1ce55478403ef34d50b4c17', [...])
              != ('e5c49f84a40f5590b06b3f4acf9c9556', [...])
        Mutant "identity is the name as typed" (in ``resolve_target``)
        failed with the same diff.
        """
        by_spelling = {}
        for spelling in ("M 31", "M31", "m31"):
            _c, plan = _compile(_graph(name=spelling), DAY_ONE)
            assert plan.targets[0].name == spelling, \
                "premise: the target still carries the name as typed"
            by_spelling[spelling] = plan
        reference = _ids(by_spelling["M31"])
        for spelling, plan in by_spelling.items():
            assert _ids(plan) == reference, \
                f"{spelling!r} is keyed apart from 'M31'"
        key = identity.name_key("M31", None)
        assert by_spelling["M 31"].targets[0].id == identity.target_id(
            identity.group_id(FLOW, "t", key), 0, 0)

    def test_control_two_different_objects_are_two_keys(self):
        """M31 and M33 are two rows, so two keys and no shared id: the
        canonical identity must still tell objects apart.

        Mutant "identity is the row's kind" (``resolve_target`` returns
        ``identity=str(row["kind"])``, so every deep-sky row is "dso")
        failed:
            AssertionError: two objects share an id
            assert {'1f52f11d563...ce9ee314bdfa'} == set()
              Extra items in the left set:
              '327b137653ab572b981675219008b330'
              ...
        """
        _c, m31 = _compile(_graph(name="M 31"), DAY_ONE)
        _c, m33 = _compile(_graph(name="M 33"), DAY_ONE)
        shared = _all_ids(m31) & _all_ids(m33)
        assert shared == set(), "two objects share an id"

    def test_jupiter_a_day_apart_in_either_case_is_one_key(self):
        """A5 kept, against the shipped ephemeris: "Jupiter" compiled a day
        apart moves on the sky and keeps every id, and "jupiter" typed on the
        second night is the same body with the same ids. No coordinate is
        printed, even on failure: only whether it moved.

        Mutant "key on the resolved coordinates" (``target_key`` returns the
        geometry key for every entry, S1's rule) failed:
            AssertionError: Jupiter a day later is keyed apart
            assert False
        Mutant "key the name as typed" failed the second half:
            AssertionError: 'jupiter' is keyed apart from 'Jupiter'
            assert False
        """
        _c, one = _compile(_graph(name="Jupiter"), DAY_ONE)
        _c, two = _compile(_graph(name="Jupiter"), DAY_TWO)
        _c, lower = _compile(_graph(name="jupiter"), DAY_TWO)
        moved = ((one.targets[0].ra_hours, one.targets[0].dec_deg)
                 != (two.targets[0].ra_hours, two.targets[0].dec_deg))
        assert moved, "premise: Jupiter moved between the compiles"
        same_day_two = _ids(two) == _ids(one)
        assert same_day_two, "Jupiter a day later is keyed apart"
        same_lower = _ids(lower) == _ids(one)
        assert same_lower, "'jupiter' is keyed apart from 'Jupiter'"

    def test_progress_finds_the_target_to_plan_keyed(self):
        """The card reads the block the run filed its frames under. The
        TARGET says "M 31"; ``to_plan`` keyed it on "M31", the catalogue's
        id, and ``progress._single`` has to key it on the same identity from
        the same resolver, or the block claims nothing and the plan reads as
        foreign.

        Mutant "progress keys the typed name" (``progress._single`` passes
        ``canonical=name``, the stripped name from the entry, in place of
        the resolver's identity) failed:
            ValueError: 1 plan target(s) match no block of this compile
            (M 31): the plan was not compiled from it with
            flow_id='flow-name-keyed', so every count read against it would
            be wrong
        Mutant "progress keys on geometry" (S1's rule) failed with the same
        refusal.
        """
        compiled, plan = _compile(_graph(name="M 31"), DAY_ONE)
        ha, lum, _red = plan.targets[0].steps
        frames = ([SessionFrame(step_id=ha.id) for _ in range(3)]
                  + [SessionFrame(step_id=lum.id)])
        session = Session(id="session-1", status="dormant", nights=["n1"],
                          plan=plan, frames=frames, origin="flow",
                          origin_id=FLOW)
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        panel, = got["blocks"][0]["panels"]
        assert panel["target_id"] == plan.targets[0].id
        assert [(s["filter"], s["banked"]) for s in panel["steps"]] == [
            ("Ha", 3), ("L", 1), ("R", 0)]


class TestTheResolversOwnRules:
    """What ``tonight.resolve_target`` does with a row, against a stubbed
    ``catalog.objects.search``: the rules are about row shapes the shipped
    catalogue does not produce today, so only a stub can reach them."""

    @pytest.fixture(autouse=True)
    def _shipped(self, monkeypatch):
        monkeypatch.setattr(tonight_module, "resolve_target", REAL_RESOLVER)

    def _answer(self, monkeypatch, row):
        from astrodeck.catalog import objects
        monkeypatch.setattr(objects, "search", lambda *a, **k:
                            objects.SearchResult(rows=[row], notes=[]))
        return tonight_module.resolve_target("anything")

    def test_a_row_id_is_trimmed_and_a_row_without_one_is_no_answer(
            self, monkeypatch):
        """The identity is the row's id, trimmed, so padding a cache file
        carries (a satellite name from an element set, say) cannot key one
        object twice; a row with no id, or a blank one, cannot be keyed and
        is no answer, as a row with no coordinates is not.

        Mutant "id not trimmed" (``identity = str(row["id"])``) failed:
            AssertionError: assert ('  ISS (ZARYA)  ', True) ==
            ('ISS (ZARYA)', True)
        Mutant "an id-less row keys as empty" (``identity =
        str(row.get("id") or "").strip()`` and the blank check removed)
        failed:
            AssertionError: assert NameResolution(ra_hours=1.0, dec_deg=2.0,
            identity='', moves=True) is None
        """
        base = {"ra_hours": 1.0, "dec_deg": 2.0, "kind": "satellite"}
        hit = self._answer(monkeypatch, {**base, "id": "  ISS (ZARYA)  "})
        assert (hit.identity, hit.moves) == ("ISS (ZARYA)", True)
        assert self._answer(monkeypatch, dict(base)) is None
        assert self._answer(monkeypatch, {**base, "id": "   "}) is None

    @pytest.mark.parametrize("kind, moves", [
        ("solar_system", True),
        ("comet", True),
        ("satellite", True),
        ("dso", False),
        ("star", False),
        ("coordinates", False),
        # A kind the catalogue does not ship today reads as fixed, so it
        # keeps ADOPT's bound until someone decides it moves.
        ("asteroid", False),
    ])
    def test_moves_is_decided_by_the_row_kind(self, monkeypatch, kind, moves):
        """Every kind the catalogue ships (``catalog/objects.py``,
        ``brightstars.py``, ``solar_system.py``, ``ephemeris/comets.py``,
        ``ephemeris/satellites.py``), each read as spec 3.3 lists it: a
        planet, the Moon, a comet and a satellite move; a deep-sky object, a
        star and a typed position do not. ``moves`` is what exempts a body
        from ADOPT's 10 arcmin bound, so a fixed kind read as moving loses
        #190's guard, and a moving kind read as fixed refuses a pre-S1
        session of it once the body has moved 10 arcmin. The shipped
        catalogue only lets a test reach ``solar_system`` (Jupiter)
        reliably: comets and satellites come from element-set cache files a
        machine may not have, so only a stub reaches them.

        Mutant "comet not a moving kind" (``MOVING_KINDS`` without
        ``"comet"``) failed here, and passed every other owned test file:
            AssertionError: assert ('comet', False) == ('comet', True)
              At index 1 diff: False != True
        Mutant "a star is a moving kind" (``MOVING_KINDS`` with ``"star"``)
        failed:
            AssertionError: assert ('star', True) == ('star', False)
              At index 1 diff: True != False
        Mutant "everything but a deep-sky row moves" (``moves=row.get("kind")
        != "dso"``) failed the star, typed-position and unknown-kind rows:
            AssertionError: assert ('coordinates', True) ==
            ('coordinates', False)
              At index 1 diff: True != False
            AssertionError: assert ('asteroid', True) == ('asteroid', False)
              At index 1 diff: True != False
        """
        hit = self._answer(monkeypatch, {"id": "X", "ra_hours": 1.0,
                                         "dec_deg": 2.0, "kind": kind})
        assert (kind, hit.moves) == (kind, moves)


class TestTargetKeyNeedsTheIdentity:
    def test_a_name_keyed_entry_with_no_canonical_identity_is_refused(self):
        """``identity.target_key`` is handed the canonical identity by its
        caller and never guesses one: a name-only entry with none raises,
        because keying the typed name (or the geometry) in its place mints
        an id the other side of the seam does not. A typed entry needs none.

        Mutant "fall back to the typed name" (``target_key`` keys
        ``name_key(canonical or name, ...)``) failed:
            Failed: DID NOT RAISE <class 'ValueError'>
        Mutant "key on the resolved coordinates" failed here too, on the
        same line: a geometry key needs no identity.
        """
        with pytest.raises(ValueError, match="canonical identity"):
            identity.target_key({"name": "M 31"}, 0.71, 41.27, None,
                                canonical=None)
        with pytest.raises(ValueError, match="canonical identity"):
            identity.target_key({"name": "M 31"}, 0.71, 41.27, None,
                                canonical="  ")
        typed = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
        assert identity.target_key(typed, 0.71, 41.27, None,
                                   canonical=None) == \
            identity.geometry_key(0.71, 41.27, None)
