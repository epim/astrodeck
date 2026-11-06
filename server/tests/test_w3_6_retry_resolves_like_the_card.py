# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The dormant retry resolves its group and its owed frames against the plan
the progress card shows (#809, #1112, consolidation WP 3.6).

``GET /api/flows/{id}/progress`` compiles the stored flow as it stands now;
``POST /api/sequence/retry-set-aside`` used to resolve against the session's
FROZEN plan. After an edit the two disagree about tonight in two ways, each
pinned below on the real app over the real stores (the harness of
test_flows_progress_route.py and test_w16_dormant_set_aside_route.py):

* A RE-FRAMED GROUP. A re-frame re-anchors the block and re-keys its ids (spec
  3.3), so the card names the group by a key the frozen plan does not hold. A
  script that posts the card's ``group_id`` got 404 "no such group"; it is the
  group the card shows, so the answer is the question's own: nothing stands
  under that key (409), exactly as the card lists nothing for it.
* A RE-KEYED STEP. An edited recipe is a new step id, so a panel the session
  banked in full under the old one owes again by the card. The card lists the
  panel and draws RETRY for it; the frozen plan counted it complete and the
  route answered 409 "nothing is set aside" to the very button drawn for it.

A session whose flow is gone, or no longer compiles into a plan, keeps its
own frozen plan: its records still stand, and a retry must not 500 on an
editor's half-built graph.

RED under the mutant "retry resolves against the frozen plan" (the dormant
branch reading ``s.plan`` where it reads ``_plan_the_card_shows(...)``),
observed:

    test_the_card_names_a_reframed_group_and_the_retry_knows_it:
        AssertionError: (404, '{"detail":"no such group"}')
    test_a_panel_the_card_lists_after_a_recipe_edit_is_one_the_retry_clears:
        AssertionError: (409, '{"detail":"nothing is set aside"}')
"""
from __future__ import annotations

import copy

import pytest

import astrodeck.api.app as app_module
from astrodeck.events import night_key
from astrodeck.flows.to_plan import GraphNotRunnable
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.session import session_store
from test_flows_progress_route import (TARGET_POOL_AND_MOSAIC, _Clock,  # noqa: F401
                                       _compiled, _frames, _local,
                                       _restore_provider, _seed, api)

REASON = "RSN-3606 gave up after repeated rejects"
ROUTE = "/api/sequence/retry-set-aside"


def _mosaic_node(graph: dict) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == "m")


def _reframed(graph: dict) -> dict:
    """The same flow with the mosaic moved 20 arcminutes: a new anchor, so a
    new group id and new panel ids."""
    out = copy.deepcopy(graph)
    _mosaic_node(out)["params"]["ra"] = "18h 20m 08s"
    return out


def _recipe_edited(graph: dict) -> dict:
    """The same flow with the mosaic's Ha exposure changed: the group and its
    panels keep their ids, every step of the block gets a new one."""
    out = copy.deepcopy(graph)
    for node in out["nodes"]:
        if node["id"] == "k":
            node["params"]["exposure"] = 420.0
    return out


async def _dormant_with_1_1_set_aside(api, monkeypatch, *, banked: bool):
    """The TARGET + POOL + mosaic flow saved, and a dormant session of it with
    a whole-panel record tonight on 1-1. ``banked``: 1-1 has also shot its
    whole quota under the ids this compile minted, so the frozen plan counts
    it complete. Returns ``(flow id, session, frozen plan, panel 1-1)``."""
    fid = await api.save_flow(TARGET_POOL_AND_MOSAIC)
    _c, plan = _compiled(TARGET_POOL_AND_MOSAIC, fid)
    p11 = next(t for t in plan.targets
               if t.mosaic_group and (t.panel_row, t.panel_col) == (0, 0))
    clock = _Clock(_local(2026, 9, 20, 16, 0))
    monkeypatch.setattr(app_module, "time", clock)
    frames = (_frames(p11.id, p11.steps[0].id, p11.steps[0].count)
              if banked else [])
    session = _seed(fid, plan, frames, created=100.0)
    session.note_set_aside(p11.id, REASON, night=night_key(clock.t),
                           kind="rejects", ts=clock.t - 300.0)
    write_json_atomic(session_store._path(session.id), session.model_dump(),
                      backup=False)
    return fid, session, plan, p11


def _mosaic_block(answer: dict) -> dict:
    return next(b for b in answer["blocks"] if "grid" in b)


async def test_the_card_names_a_reframed_group_and_the_retry_knows_it(
        api, monkeypatch):
    """The flow is re-framed after the night: the card's mosaic block carries
    a group id the session's frozen plan does not hold, and lists no set-aside
    panel (nothing stands under the new ids). Posting that group id with the
    session's id is the question 'what is set aside in the group the card
    shows', and the answer is 409 'nothing is set aside', as the card says,
    never 404 'no such group' for a group the card names."""
    fid, session, frozen, _p11 = await _dormant_with_1_1_set_aside(
        api, monkeypatch, banked=False)
    await api.put_flow(fid, _reframed(TARGET_POOL_AND_MOSAIC))
    block = _mosaic_block(await api.ok(fid))
    frozen_groups = {g.id for g in frozen.groups}
    assert block["group_id"] not in frozen_groups, (
        "premise: the re-frame re-keyed the group")
    assert "set_aside" not in block, "premise: the card lists nothing"

    res = await api.client.post(ROUTE, json={"group": block["group_id"],
                                             "session_id": session.id})
    assert (res.status_code, res.text.strip()) == (
        409, '{"detail":"nothing is set aside"}')
    stored = session_store.load(session.id)
    assert [r.get("cleared") for r in stored.set_aside] == [None], (
        "a refused retry changed the record")


async def test_a_panel_the_card_lists_after_a_recipe_edit_is_one_the_retry_clears(
        api, monkeypatch):
    """1-1 shot its whole quota under the old recipe and is set aside by a
    record. The flow's Ha exposure is then edited: by the card the panel owes
    again and is LISTED, with the retry it draws. The retry for the card's
    group and the session clears exactly that panel."""
    fid, session, _frozen, p11 = await _dormant_with_1_1_set_aside(
        api, monkeypatch, banked=True)
    assert session.owed() > 0, "premise: the other panels still owe"
    await api.put_flow(fid, _recipe_edited(TARGET_POOL_AND_MOSAIC))
    block = _mosaic_block(await api.ok(fid))
    assert [e["target_id"] for e in block["set_aside"]] == [p11.id], (
        "premise: the card lists 1-1 as set aside and owing")

    res = await api.client.post(ROUTE, json={"group": block["group_id"],
                                             "session_id": session.id})
    assert res.status_code == 200, (res.status_code, res.text)
    assert res.json() == {"cleared": [SequenceEngine._panel_name(p11)],
                          "live": False}
    stored = session_store.load(session.id)
    assert [r.get("cleared") for r in stored.set_aside] == [True]


async def test_a_session_whose_flow_is_gone_is_retried_by_its_frozen_plan(
        api, monkeypatch):
    """The flow was deleted: there is no card to agree with, and the
    session's records still stand under its own plan, so the retry resolves
    against that plan, as it always did."""
    fid, session, frozen, p11 = await _dormant_with_1_1_set_aside(
        api, monkeypatch, banked=False)
    group = frozen.groups[0].id
    gone = await api.client.delete(f"/api/flows/{fid}")
    assert gone.status_code == 200, gone.text
    assert (await api.progress(fid)).status_code == 404, (
        "premise: the flow is gone")

    res = await api.client.post(ROUTE, json={"group": group,
                                             "session_id": session.id})
    assert res.status_code == 200, (res.status_code, res.text)
    assert res.json() == {"cleared": [SequenceEngine._panel_name(p11)],
                          "live": False}


async def test_a_flow_that_no_longer_compiles_does_not_block_the_retry(
        api, monkeypatch):
    """An editor's half-built graph is a normal state (the card answers it
    422): the retry still clears the session's records by the plan the
    session froze, instead of failing with the graph."""
    _fid, session, frozen, p11 = await _dormant_with_1_1_set_aside(
        api, monkeypatch, banked=False)

    def refuse(*_a, **_kw):
        raise GraphNotRunnable("the graph has no target")
    monkeypatch.setattr(app_module, "to_sequence_plan", refuse)

    res = await api.client.post(ROUTE, json={"group": frozen.groups[0].id,
                                             "session_id": session.id})
    assert res.status_code == 200, (res.status_code, res.text)
    assert res.json() == {"cleared": [SequenceEngine._panel_name(p11)],
                          "live": False}
