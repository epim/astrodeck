# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The progress ROUTE and a dormant session's set-aside panels (#727; backlog
wave 16, WP-141): the seam test_w16_dormant_set_aside_progress.py cannot see.

That file grades ``flow_progress(..., now=)`` pure, on a plan it compiles
itself. This one runs the real app over the real stores (the harness of
test_flows_progress_route.py): a flow saved through the API, a dormant session
seeded on disk under the ids that flow's own compile mints, the app's clock
pinned, and GET /api/flows/{id}/progress. Two things only the route can show:

* THE PANELS LISTED ARE PANELS THE DORMANT RETRY CLEARS. A button drawn for a
  listed panel must never answer 409 "nothing is set aside", so the same
  seeded session is then given to ``POST /api/sequence/retry-set-aside`` with
  the block's ``group_id`` and the session's id, and every panel the block
  listed is among the labels it clears. (The route clears MORE: it clears a
  panel whose only record is a step's, which the block does not list, because
  the panel is still shot.)
* THE ROUTE HANDS ITS CLOCK IN. ``flow_progress`` reads no clock of its own,
  so the key appears only when ``_flow_progress_payload`` passes ``now``, the
  way it passes the clock to ``continue_night``.

WP-141's brief owned progress.py, group_rules.py and PanelsSection.tsx and not
app.py, whose ``_flow_progress_payload`` is the one line that wires it
(``flow_progress(compiled, plan, session, flow_id=flow_id, now=now)``). So the
first case below stands in for that one line, wrapping the route's
``flow_progress`` with the app's own pinned clock, and proves the rest of the
chain end to end; the second is the case that asks for the real wiring and is
``xfail(strict=True)`` until it lands: the day ``_flow_progress_payload``
passes ``now`` it XPASSes, which fails the suite with a message, and the
marker is then deleted. NOTHING ELSE changes with the wiring: every answer
without a standing record is the answer it was (the allow-list and the type
mirror walk sessions with none).
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.events import night_key
from astrodeck.flows.progress import flow_progress as real_flow_progress
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.session import session_store
from test_flows_progress_route import (TARGET_POOL_AND_MOSAIC, _Clock,  # noqa: F401
                                       _compiled, _frames, _local,
                                       _restore_provider, _seed, api)

REASON = "RSN-5520 gave up after repeated rejects"


async def _seeded(api, monkeypatch):
    """A dormant session of the TARGET + POOL + mosaic flow with, tonight,
    a whole-panel record on 1-1 (owing) and one on 2-1, which has banked its
    whole quota (the retry leaves a panel that owes nothing alone); and a
    whole-panel record on 1-2 from LAST night, which is history (the retry
    reads tonight's records only). Returns ``(flow id, session, plan, the
    three panels)``."""
    fid = await api.save_flow(TARGET_POOL_AND_MOSAIC)
    _c, plan = _compiled(TARGET_POOL_AND_MOSAIC, fid)
    panels = {(t.panel_row, t.panel_col): t for t in plan.targets
              if t.mosaic_group}
    p11, p12, p21 = panels[(0, 0)], panels[(0, 1)], panels[(1, 0)]
    clock = _Clock(_local(2026, 9, 20, 16, 0))
    monkeypatch.setattr(app_module, "time", clock)
    night = night_key(clock.t)
    frames = _frames(p21.id, p21.steps[0].id, p21.steps[0].count)
    session = _seed(fid, plan, frames, created=100.0)
    session.note_set_aside(p11.id, REASON, night=night, kind="rejects",
                           ts=clock.t - 300.0)
    session.note_set_aside(p12.id, REASON,
                           night=night_key(clock.t - 24 * 3600.0),
                           kind="rejects")
    session.note_set_aside(p21.id, REASON, night=night, kind="rejects")
    write_json_atomic(session_store._path(session.id), session.model_dump(),
                      backup=False)
    return fid, session, plan, (p11, p12, p21)


def _wire_the_clock_in(monkeypatch) -> None:
    """Stand in for ``_flow_progress_payload`` passing ``now`` to
    ``flow_progress``: the app's own (pinned) clock, read once per call.
    ``setdefault``, so the day the route passes ``now`` itself this wrapper
    leaves that value alone instead of raising "multiple values for keyword
    argument 'now'" (which would break this case beside the xfail one)."""
    def with_now(*a, **kw):
        kw.setdefault("now", app_module.time.time())
        return real_flow_progress(*a, **kw)
    monkeypatch.setattr(app_module, "flow_progress", with_now)


def _mosaic(answer: dict) -> dict:
    return next(b for b in answer["blocks"] if "grid" in b)


async def test_the_listed_panels_are_panels_the_dormant_retry_clears(
        api, monkeypatch):
    """With the clock handed in, the block lists 1-1 and only 1-1 (1-2's
    record is last night's, 2-1 owes nothing), the entry carries no word of
    the record's reason, and a retry for that group and session clears 1-1,
    never a 409, and leaves 2-1 alone.

    RED under mutant "any night" (``_set_aside_tonight`` reading every
    record not expired and not cleared, whatever its night, so 1-2 is
    listed), observed:

        AssertionError: assert ['930d389a553...644a9578bfeb'] ==
        ['930d389a553...365914253027']

    RED under mutant "complete panels listed" (the owed test dropped, so 2-1
    is listed), observed:

        AssertionError: assert ['acb293d4346...b075a43c9144'] ==
        ['acb293d4346...166830750baf']

    RED under mutant "set_aside never listed", observed: KeyError:
    'set_aside'. The last assertion, that every panel listed is one the
    retry clears, is the cross-component pin: it goes red only under a
    change to the retry route's own rules (its night, its membership), which
    is app.py's and is not mutated here.
    """
    fid, session, plan, (p11, p12, p21) = await _seeded(api, monkeypatch)
    _wire_the_clock_in(monkeypatch)
    answer = await api.ok(fid)
    block = _mosaic(answer)
    assert [e["target_id"] for e in block["set_aside"]] == [p11.id]
    assert "RSN-5520" not in str(answer)
    assert answer["session"]["id"] == session.id
    assert answer["session"]["status"] == "dormant"
    assert block["group_id"], "the block names the group the retry takes"

    res = await api.client.post("/api/sequence/retry-set-aside", json={
        "group": block["group_id"], "session_id": answer["session"]["id"]})
    assert res.status_code == 200, res.text
    cleared = set(res.json()["cleared"])
    listed = {SequenceEngine._panel_name(plan_target)
              for plan_target in plan.targets
              if plan_target.id in {e["target_id"] for e in block["set_aside"]}}
    assert listed <= cleared, (listed, cleared)
    assert SequenceEngine._panel_name(p21) not in cleared, (
        "a panel that owes nothing is left alone by the retry, and by the block")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "needs app.py _flow_progress_payload to pass now= to flow_progress "
    "(WP-141 blocked_on: app.py is not that work package's file); delete "
    "this marker when it does"))
async def test_the_route_hands_the_clock_in(api, monkeypatch):
    """The real wiring, no stand-in: GET progress on the seeded dormant
    session lists 1-1 under the pinned app clock. Until the route passes
    ``now`` the answer is the answer before #727 and this fails; the day it
    does, the XPASS(strict) says to delete the marker."""
    fid, _session, _plan, (p11, _p12, _p21) = await _seeded(api, monkeypatch)
    block = _mosaic(await api.ok(fid))
    assert [e["target_id"] for e in block.get("set_aside", [])] == [p11.id]
