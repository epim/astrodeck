# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#195 (WP-118, part D): DUSK WINDOW's `repeat` is retired. The `campaign`
compile block, the doctor's `_is_campaign` and Tonight's campaign rows key on
Automatic resume plus the flow's own shape, and nothing reads `repeat`.

THE RULING (backlog ruling D-nn, owner-approved 2026-09-30; spec Revision 2,
ruling 7): a POOL with Automatic resume On is a campaign until the pool is
complete; any other flow with On resumes as it always has, with no campaign
block. A campaign is therefore not a longer night and not a word a retired
select once held: it is "a pool that comes back". The `until: nights_30` shape
is gone, because nothing ever counted nights (`to_plan` said so in a note).

WHY IT IS A TEST OF MANY GRAPHS, not of one. Four places used to decide
"campaign" from `repeat`, each with its own copy of the test: the compile, the
doctor, Tonight's CAMPAIGN tab and the brief's two campaign sentences. The
defect that matters is the four drifting apart (a CAMPAIGN tab that says "this
is a campaign" over a plan that compiled none), so every case here asks all of
them about the SAME graph and holds the answers to each other, over a matrix
of the option crossed with every `repeat` a stored file can hold.

A STORED `repeat` IS NOT MIGRATED BY A WRITE. 'Nightly until pool complete',
'Nightly x30' and 'Single night' all read as Automatic resume On, which is what
the missing `autoResume` means (`nodes.dusk_auto_resume`), so the read has
nothing to rewrite and, above all, never maps any of them to Off, which would
silently disarm a flow its operator never asked to stop. No FLOW_SCHEMA number
is taken: nothing a build older than this one reads differently changes the
night (the matrix of spec 3.6 says which row it is).

NAMED MUTANTS (each run from a byte backup inside this worktree, restored and
sha256-compared, the mutant text grepped out afterwards; the failing assertion
is quoted in the docstring of the test that catches it):

* "campaign keyed on repeat": `compile.campaign_block` reads `repeat` again;
* "campaign ignores the option": `campaign_block` drops the Automatic resume
  test;
* "campaign without a pool": `campaign_block` drops the pool test;
* "nights_30 returns": `campaign_block` writes `until` from `repeat`'s words;
* "doctor keys on repeat": `doctor._is_campaign` reads `repeat` again;
* "tonight keys on repeat": `tonight._campaign`'s ``is_campaign`` (the pool
  path) reads `repeat` again;
* "tonight mosaic keys on repeat": the same, on the pool-less mosaic path;
* "brief keys on repeat": `tonight.brief`'s two campaign sentences read
  `repeat` again;
* "quota claimed for a capture pool": the brief names the pool's cycle quota
  for a pool that has no FILTER CYCLE;
* "repeat declared again": `repeat` back in the DUSK WINDOW vocabulary;
* "migration maps a stored repeat to Off": `store._migrate` writes
  ``autoResume: "Off"`` into a DUSK that stores a campaign `repeat`;
* "spec row removed": the downgrade matrix's 'Nightly x30' row loses its words;
* "spec still lists not built": the ruling 7 paragraph's old "Not built:" list
  comes back.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import astrodeck.config as config_mod
from astrodeck.flows.compile import campaign_block, compile_plan
from astrodeck.flows.doctor import _is_campaign
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowGraph, FlowNode
from astrodeck.flows.nodes import NODE_DEFS, create_params, dusk_auto_resume
from astrodeck.flows.store import (AUTO_RESUME_NOTE, FLOW_SCHEMA, FlowStore,
                                   schema_for)
from astrodeck.flows.tonight import _DAWN, _DAWN_OFF, _campaign, brief
from astrodeck.persist import ensure_dir

SPEC = (Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs"
        / "2026-09-23-flows-mosaic-target-block-design.md")

#: The shape a pool campaign compiles to, and the only one there is.
CAMPAIGN = {"repeat": "nightly", "until": "pool_complete", "resume": "cursor"}

#: Every `repeat` a stored file can hold. The editor stopped offering the
#: select in 0.3.41 and this build stops declaring the param, so each of these
#: can only come from a file written before.
REPEATS = ("Single night", "Nightly until pool complete", "Nightly x30",
           "Nightly ×30", "something no build ever offered")

#: What the DUSK WINDOW of a stored file says about Automatic resume: nothing
#: (every file older than 0.3.41), On, and Off. Each is a dict of the params
#: the file holds, so a state can leave `autoResume` out, which a default
#: merge would otherwise fill in.
ON_STATES = ({}, {"autoResume": "On"}, {"autoResume": ""},
             {"autoResume": "a value this build does not offer"})
OFF_STATES = ({"autoResume": "Off"},)


def _example(flow_id: str) -> FlowGraph:
    return next(e for e in examples() if e.id == flow_id).graph.model_copy(
        deep=True)


def _stored(graph: FlowGraph, **dusk_params) -> FlowGraph:
    """``graph`` with every DUSK WINDOW holding EXACTLY ``dusk_params``: what
    a stored file holds, with no created default beneath it. The read fills a
    missing key in (``with_defaults``), as it does for a real file."""
    for n in graph.nodes:
        if n.type == "dusk":
            n.params = dict(dusk_params)
    return graph


def _without_dusk(graph: FlowGraph) -> FlowGraph:
    keep = {n.id for n in graph.nodes if n.type != "dusk"}
    return FlowGraph(
        nodes=[n for n in graph.nodes if n.id in keep],
        edges=[e for e in graph.edges if e.from_ in keep and e.to in keep])


def _matrix():
    """(label, option states, repeat) over the pool flow and the flows that
    have none: the crossing the four readers must agree on."""
    for state in ON_STATES + OFF_STATES:
        for repeat in (None,) + REPEATS:
            params = dict(state)
            if repeat is not None:
                params["repeat"] = repeat
            yield params


def _campaigns_by_every_reader(graph: FlowGraph) -> dict:
    """What each of the four readers says of this graph."""
    c = _campaign(graph, None)
    return {
        "compile": "campaign" in compile_plan(graph),
        "doctor": _is_campaign(graph) is not None,
        "tonight": c["is_campaign"],
    }


# ======================================================== the compile's block

@pytest.mark.parametrize("params", [dict(s) for s in ON_STATES])
def test_a_pool_with_automatic_resume_on_is_a_campaign_until_the_pool_is_complete(
        params):
    """THE RULING. A POOL whose DUSK WINDOW does not say Off compiles the
    campaign block, with `until` the pool's completion and nothing else, and
    says nothing about `repeat`, which the flow never stored.

    MUTANT "campaign keyed on repeat" (`campaign_block`'s Automatic resume
    test made ``str(dusk.params.get("repeat") or "Single night") == "Single
    night"``) turned this red, run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: a pool whose Automatic resume is unset must compile
        the campaign block: None
        assert None == {'repeat': 'nightly', 'resume': 'cursor', 'until':
        'pool_complete'}
    """
    plan = compile_plan(_stored(_example("example-pool"), **params))
    assert plan.get("campaign") == CAMPAIGN, (
        f"a pool whose Automatic resume is {params or 'unset'} must compile "
        f"the campaign block: {plan.get('campaign')!r}")
    assert "resume_across_nights" not in plan, (
        "an On flow writes no resume_across_nights: its compile is the "
        "compile before the option existed")


def test_a_pool_with_automatic_resume_off_compiles_no_campaign():
    """OFF IS NOT A CAMPAIGN. The block's own note says "goes dormant with
    auto-resume armed, and comes back the next night" (`to_plan`), which is
    exactly what an Off flow is told not to do, so it carries no block, and
    `resume_across_nights` says False.

    MUTANT "campaign ignores the option" (`campaign_block`'s first test cut
    to ``if dusk is None:``) turned this red, run from a byte backup and
    restored and sha256-verified afterwards:

        AssertionError: an Off flow stored with no repeat compiled a
        campaign: {'repeat': 'nightly', 'until': 'pool_complete', 'resume':
        'cursor'}
    """
    for repeat in (None,) + REPEATS:
        params = {"autoResume": "Off"}
        if repeat is not None:
            params["repeat"] = repeat
        plan = compile_plan(_stored(_example("example-pool"), **params))
        assert "campaign" not in plan, (
            f"an Off flow stored with "
            f"{'no repeat' if repeat is None else f'repeat {repeat!r}'} "
            f"compiled a campaign: {plan.get('campaign')!r}")
        assert plan.get("resume_across_nights") is False


@pytest.mark.parametrize("repeat", REPEATS)
def test_repeat_decides_nothing_about_the_campaign(repeat):
    """THE DEFECT CLASS: a retired word still keying a block. With Automatic
    resume On the pool is a campaign whatever `repeat` says, "Single night"
    included (it used to be the one value that was not); with Off it is none
    whatever `repeat` says, "Nightly until pool complete" included (it used
    to be the one value that was).

    MUTANT "campaign keyed on repeat" turned this red, for the "Single
    night" row (the On pool compiled none):

        AssertionError: ('Single night', None)
        assert None == {'repeat': 'nightly', 'resume': 'cursor', 'until':
        'pool_complete'}

    and MUTANT "campaign ignores the option" for the same row, from the
    other side (the Off pool compiled one):

        AssertionError: ('Single night', {'repeat': 'nightly', 'resume':
        'cursor', 'until': 'pool_complete'})
        assert 'campaign' not in {...}
    """
    on = compile_plan(_stored(_example("example-pool"), repeat=repeat))
    off = compile_plan(_stored(_example("example-pool"), repeat=repeat,
                               autoResume="Off"))
    assert on.get("campaign") == CAMPAIGN, (repeat, on.get("campaign"))
    assert "campaign" not in off, (repeat, off.get("campaign"))


@pytest.mark.parametrize("params", list(_matrix()))
def test_a_flow_without_a_pool_compiles_no_campaign_whatever_it_stored(params):
    """A campaign is a POOL that comes back. A single TARGET, a mosaic and an
    EAA flow with On resume as they always have, and say no more about it:
    their plan has no `campaign` key (the old `nights_30` shape, which a
    'Nightly x30' flow without a pool used to compile, is gone).

    MUTANT "campaign without a pool" (`campaign_block`'s pool test made
    ``if False:``) turned this red, run from a byte backup and restored and
    sha256-verified afterwards: a single-target M31 flow compiled a campaign
    that ends when a pool it does not have is complete,

        AssertionError: ('example-m31', {}, {'repeat': 'nightly', 'resume':
        'cursor', 'until': 'pool_complete'})
        assert 'campaign' not in {...}
    """
    for example in ("example-m31", "example-m31-mosaic", "example-m16"):
        plan = compile_plan(_stored(_example(example), **params))
        assert "campaign" not in plan, (example, params, plan.get("campaign"))


def test_a_flow_with_no_dusk_window_has_no_campaign():
    """No DUSK WINDOW carries no opinion about resuming, so a pool without one
    is not a campaign either (the campaign Example is the control: it is)."""
    assert compile_plan(_example("example-campaign")).get("campaign") == CAMPAIGN
    plan = compile_plan(_without_dusk(_example("example-campaign")))
    assert "campaign" not in plan and "resume_across_nights" not in plan


@pytest.mark.parametrize("repeat", REPEATS)
def test_until_is_never_a_count_of_nights(repeat):
    """`until` was `nights_30` for any `repeat` that was not a pool's, a stop
    condition nothing enforced (`to_plan` said "Nothing counts NIGHTS"). The
    block has one shape now.

    MUTANT "nights_30 returns" (`campaign_block` writing `until` from
    `repeat`'s own words, `pool_complete` only when they name a pool) turned
    this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: assert 'nights_30' == 'pool_complete'
    """
    plan = compile_plan(_stored(_example("example-pool"), repeat=repeat))
    assert plan["campaign"]["until"] == "pool_complete"


def test_campaign_block_is_the_one_decision_the_compile_writes():
    """The helper the doctor and Tonight read is the helper `compile_plan`
    writes from, so a reader cannot disagree with the plan by construction;
    and a graph with no DUSK WINDOW or no pool answers None."""
    g = _stored(_example("example-pool"), autoResume="On")
    assert campaign_block(g.with_defaults()) == CAMPAIGN
    assert campaign_block(g.with_defaults()) == compile_plan(g)["campaign"]
    assert campaign_block(_without_dusk(g).with_defaults()) is None
    assert campaign_block(
        _stored(_example("example-m31"), autoResume="On").with_defaults()
    ) is None


# ================================================== the readers agree

@pytest.mark.parametrize("params", list(_matrix()))
def test_the_compile_the_doctor_and_tonight_agree_on_what_a_campaign_is(params):
    """FOUR READERS, ONE ANSWER. Over the option crossed with every stored
    `repeat`, on a pool flow (a campaign exactly when the option is not Off)
    and on a flow with no pool (never one), the compile's block, the doctor's
    `_is_campaign` and Tonight's `is_campaign` all say the same thing.

    MUTANT "doctor keys on repeat" (`doctor._is_campaign` reading `repeat`
    again) turned this red, run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: ({}, {'compile': True, 'doctor': False, 'tonight':
        True})

    MUTANT "tonight keys on repeat" (`tonight._campaign`'s ``is_campaign``
    from `repeat`, the pool path) turned it red the other way round,

        AssertionError: ({}, {'compile': True, 'doctor': True, 'tonight':
        False})

    and MUTANT "tonight mosaic keys on repeat" (the pool-less mosaic path's
    ``is_campaign`` from `repeat`) turned it red on the no-pool flows:

        AssertionError: ('example-m31-mosaic', {'repeat': 'Nightly until pool
        complete'}, {'compile': False, 'doctor': False, 'tonight': True})
    """
    off = params.get("autoResume") == "Off"
    pool = _campaigns_by_every_reader(_stored(_example("example-pool"), **params))
    assert pool == {"compile": not off, "doctor": not off, "tonight": not off}, (
        params, pool)
    for example in ("example-m31", "example-m31-mosaic"):
        none = _campaigns_by_every_reader(_stored(_example(example), **params))
        assert none == {"compile": False, "doctor": False, "tonight": False}, (
            example, params, none)


def test_the_doctors_campaign_is_the_dusk_window_that_makes_it_one():
    """`_is_campaign` answers the DUSK WINDOW node, or None: a rule that needs
    the distinction reads the node's id from it.

    MUTANT "doctor keys on repeat" turned this red:
    `assert (None is not None)`, the On pool stored with no `repeat`."""
    g = _stored(_example("example-pool"), autoResume="On")
    node = _is_campaign(g)
    assert node is not None and node.type == "dusk"
    assert _is_campaign(_stored(_example("example-pool"),
                                autoResume="Off")) is None


# ============================================================ Tonight's rows

def test_a_pool_campaign_ends_its_note_with_the_dusk_resume_and_an_off_pool_does_not():
    """The CAMPAIGN tab's note for a pool, On: it is a campaign and the dawn
    clause is `_DAWN`; Off: it is not, and the note says what Off does. The
    option decides both, so the row and the sentence cannot disagree.

    MUTANT "tonight keys on repeat" (`_campaign`'s ``is_campaign`` read from
    `repeat` again) turned this red, run from a byte backup and restored and
    sha256-verified afterwards: the On pool (stored "Single night") was not a
    campaign, and the note was the not-a-campaign note.
    """
    on = _campaign(_stored(_example("example-pool"), autoResume="On",
                           repeat="Single night"), None)
    assert on["is_campaign"] is True and on["has_pool"] is True
    assert on["note"].endswith(_DAWN), on["note"]
    off = _campaign(_stored(_example("example-pool"), autoResume="Off",
                            repeat="Nightly until pool complete"), None)
    assert off["is_campaign"] is False and off["has_pool"] is True
    assert "Automatic resume is off" in off["note"], off["note"]
    assert _DAWN_OFF.split(". ")[-1] not in on["note"]


def test_a_mosaic_without_a_pool_is_no_campaign_on_the_tab():
    """A pool-less mosaic resumes as any On flow does, and the tab still
    speaks of its panels, but it is not a campaign: the ruling names a POOL.
    It said it was one whenever `repeat` was set.

    MUTANT "tonight mosaic keys on repeat" (the pool-less mosaic return's
    ``is_campaign`` read from `repeat` again) turned this red, run from a byte
    backup and restored and sha256-verified afterwards: `assert (True is
    False)` for the mosaic Example stored with "Nightly until pool complete".
    """
    g = _stored(_example("example-m31-mosaic"), repeat="Nightly until pool complete")
    c = _campaign(g, None)
    assert c["is_campaign"] is False and c["has_pool"] is False
    assert "panels" in c


REARMS = "the flow re-arms at the next dusk and resumes mid-cycle"
PARKED = "the rig stays parked"


def test_the_briefs_campaign_sentences_follow_the_campaign_block():
    """The brief's two campaign sentences, the re-arm clause on the shutdown
    lane and "Once all N targets hold their quota, the rig stays parked", are
    said for a campaign and for nothing else: a pool with Automatic resume
    On (whatever it stored for `repeat`), and not for an Off pool, even one
    stored with "Nightly until pool complete".

    MUTANT "brief keys on repeat" (`campaign = campaign_block(g)` made None
    for a `repeat` of "Single night", as the old test read it) turned this red,
    run from a byte backup and restored and sha256-verified afterwards: the On
    pool stored with "Single night" said neither sentence,

        assert ('the flow re-arms at the next dusk and resumes mid-cycle' in
        'This flow arms at astronomical dusk ...' and 'the rig stays parked'
        in ...)
    """
    on = brief(_stored(_example("example-campaign"), autoResume="On",
                       repeat="Single night"))
    assert REARMS in on and PARKED in on, on
    off = brief(_stored(_example("example-campaign"), autoResume="Off",
                        repeat="Nightly until pool complete"))
    assert REARMS not in off and PARKED not in off, off
    # The control that is not a campaign: no pool, so neither sentence.
    single = brief(_stored(_example("example-m16"), autoResume="On",
                           repeat="Nightly until pool complete"))
    assert REARMS not in single and PARKED not in single, single


def test_the_pool_complete_sentence_names_the_quota_only_where_it_governs():
    """The pool's `quota` is a count of CYCLES and reaches the run through a
    FILTER CYCLE alone; a CAPTURE LOOP under a pool ends each member at its own
    frame count. The sentence said "hold their 45-cycle quota" for the one
    flow that stored a `repeat`, a cycle pool, where it is true. Keyed on the
    campaign block it reaches EVERY pool flow with Automatic resume On, and
    for a capture-loop pool the number is a finish line the run never reads,
    so that pool is told the frames it asks for instead.

    MUTANT "quota claimed for a capture pool" (`brief`'s ``if any(x.type ==
    "cycle" ...)`` made ``if True:``) turned this red, run from a byte backup
    and restored and sha256-verified afterwards:

        AssertionError: assert 'Once all 4 targets have the frames they ask
        for, the rig stays parked.' in 'This flow arms at astronomical dusk
        ...'
    """
    cycle = brief(_example("example-campaign"))
    assert "Once all 4 targets hold their 45-cycle quota, the rig stays parked." in cycle
    capture = brief(_stored(_example("example-pool"), autoResume="On"))
    assert "Once all 4 targets have the frames they ask for, the rig stays parked." in capture
    assert "cycle quota" not in capture, (
        "a pool with no FILTER CYCLE was told a cycle quota: " + capture)


# ============================================================ the vocabulary

def test_dusk_window_no_longer_declares_repeat():
    """`repeat` is retired from the vocabulary: a new DUSK WINDOW is created
    without it, and a stored one keeps whatever it holds, verbatim, because an
    unknown stored param is never dropped (`FlowNode.with_defaults` merges the
    defaults UNDER the stored params) and nothing reads it.

    MUTANT "repeat declared again" (`"repeat": "Single night"` back in the
    DUSK params in nodes.py) turned this red, run from a byte backup and
    restored and sha256-verified afterwards:

        AssertionError: assert 'repeat' not in {'autoResume': 'On', 'minAlt':
        30, 'offset': -30, 'repeat': 'Single night', ...}
    """
    assert "repeat" not in NODE_DEFS["dusk"].params
    assert "repeat" not in create_params("dusk")
    node = FlowNode(id="d", type="dusk", x=0, y=0,
                    params={"repeat": "Nightly x30"}).with_defaults()
    assert node.params["repeat"] == "Nightly x30"
    assert node.params["autoResume"] == "On"
    assert "repeat" not in FlowNode(id="d", type="dusk", x=0,
                                    y=0).with_defaults().params


@pytest.mark.parametrize("repeat", REPEATS)
def test_a_stored_repeat_reads_as_automatic_resume_on(repeat):
    """Ruling 2: every stored `repeat` reads as On, and none reads as Off."""
    assert dusk_auto_resume({"repeat": repeat}) is True
    assert dusk_auto_resume({"repeat": repeat, "autoResume": "On"}) is True
    assert dusk_auto_resume({"repeat": repeat, "autoResume": "Off"}) is False


# ================================================================ the store

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


def _file(directory, fid: str, schema_version: int, dusk: dict):
    ensure_dir(directory)
    graph = {"nodes": [
        {"id": "d", "type": "dusk", "x": 30, "y": 60, "params": dict(dusk)},
        {"id": "t", "type": "target", "x": 260, "y": 60,
         "params": {"name": "NGC 7129", "ra": "21h 42m 30s",
                    "dec": "+66 06 00", "rotation": -1,
                    "counts": "Accepted subs"}},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100,
                    "bin": "1", "count": 12, "goal": 0}}],
        "edges": [
            {"from": "d", "fromPort": "window", "to": "t", "toPort": "arm"},
            {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"}]}
    path = directory / f"{fid}.json"
    path.write_text(json.dumps({"schema_version": schema_version, "id": fid,
                                "flow": {"id": fid, "name": f"flow {fid}",
                                         "folder": "My flows",
                                         "graph": graph}}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("repeat", [r for r in REPEATS if r != "Single night"])
def test_a_stored_campaign_repeat_is_read_as_on_and_rewritten_as_nothing(
        store, repeat):
    """RULING 2, at the store. A flow written by a build that offered the
    select, with a campaign `repeat` and no `autoResume`, reads with NO note
    (nothing about it changed that an operator needs to decide: it resumed on
    later nights then and does now), with the graph untouched, as On, and
    compiles no `resume_across_nights`. It is never written as Off, and a save
    of it takes no new FLOW_SCHEMA number.

    MUTANT "migration maps a stored repeat to Off" (`_migrate` writing
    ``autoResume: "Off"`` into a DUSK whose stored `repeat` is not "Single
    night") turned this red, run from a byte backup and restored and
    sha256-verified afterwards:

        AssertionError: the read rewrote the DUSK: it must only say what the
        missing key means
        assert {'autoResume'...ool complete'} == {'repeat': 'N...ool complete'}
    """
    _file(store.dir, "f1", 4, {"repeat": repeat})
    rec = store.get("f1")
    assert [n.key for n in rec.migrated] == [], (
        f"a stored {repeat!r} says nothing a note should: "
        f"{[n.model_dump() for n in rec.migrated]}")
    assert rec.graph.node("d").params == {"repeat": repeat}, (
        "the read rewrote the DUSK: it must only say what the missing key means")
    assert dusk_auto_resume(rec.graph.node("d").params) is True
    assert "resume_across_nights" not in compile_plan(rec.graph)
    assert schema_for(rec.graph) != FLOW_SCHEMA, (
        "5 is Off, or a DUSK that owes the 'Single night' note; neither is "
        "this graph, and stamping it would refuse the file on a build that "
        "reads it the same")


def test_a_stored_single_night_still_owes_its_note_and_nothing_else_does(store):
    """The one note there is stays the one note: 'Single night' (wave 14),
    verbatim. Retiring `repeat` adds no second."""
    _file(store.dir, "f1", 4, {"repeat": "Single night"})
    assert [n.model_dump() for n in store.get("f1").migrated] == [
        {"key": "autoResume", "note": AUTO_RESUME_NOTE}]


# =================================================================== the spec

def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_the_spec_says_the_nights_30_shape_is_gone_in_the_downgrade_matrix():
    """Ruling 1 of the plan: "The `until: nights_30` shape is gone; say so in
    the spec's downgrade matrix." The matrix has a row for the stored
    'Nightly x30', and says what each reader does with it.

    MUTANT "spec row removed" (the row's "Nightly x30" made "Nightly 30")
    turned this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: the downgrade matrix has no row for the stored
        'Nightly x30'
    """
    text = _spec()
    matrix = text.split("**Downgrade matrix**", 1)[1].split("###", 1)[0]
    rows = [r for r in matrix.splitlines() if r.startswith("|")]
    row = next((r for r in rows if "Nightly x30" in r), None)
    assert row is not None, (
        "the downgrade matrix has no row for the stored 'Nightly x30'")
    assert "nights_30" in row and "pool_complete" in row, row
    assert re.search(r"no campaign block|no `campaign`", row), row


def test_the_spec_no_longer_lists_the_campaign_keying_as_not_built():
    """Ruling 7's "As built" ended with "Not built: the `campaign` block, the
    Tonight campaign rows and `_is_campaign` keyed on the option, and the
    retirement of `repeat`". They are built (WP-118), so the paragraph says
    so, and what is still not built is the wizard's step and the CONTINUE
    surfaces' wiring of the RUN notice, not these.

    MUTANT "spec still lists not built" (the comma and the semicolon of the
    old list put back) turned this red:

        AssertionError: assert 'keyed on th...of `repeat`;' not in ' automatic
        ...'
    """
    text = _spec()
    ruling7 = text.split("### Ruling 7:", 1)[1].split("### Ruling 8:", 1)[0]
    assert "keyed on the option, and the retirement of `repeat`;" not in ruling7
    assert "WP-118" in ruling7 and "campaign_block" in ruling7
