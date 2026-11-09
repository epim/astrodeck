# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A REFOCUS THAT FALLS DUE MID-VISIT ON A STAR-POOR PANEL WAITS FOR THE NEXT
HOP, WHICH PLACES IT (WP 5.3, #185).

`test_w5_mosaic_focus.py` covers a sweep owed AT a hop. The frame loop has its
own refocus (``autofocus_every``, the temperature delta), taken at a frame
boundary on whatever panel the visit is on, and on a panel known to be
star-poor it fails as a sparse field, which under ``af_failure_action`` skip
costs the panel its visit. Left owed instead, the next hop asks
`_hop_focus_is_owed`, which finds `_refocus_due` still true and has
`_focus_site` put the sweep on the richest panel.

The run is the real clocked night (tests/_group_harness.py): a 2x2 of L and R
with ``autofocus_every`` 1, the panels reporting their own star counts through
the frames' ``info`` (50 reads as nothing here: 2-2 reports 5, 1-1 100, 2-1 200
and 1-2 500). The harness has no focuser, so one is added and ``run_autofocus``
scripted to succeed and to record where the mount stood.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.sequence.session import session_store
from test_group_plain_stop_defers import _Focuser

POOR_PANEL = f"{GROUP_NAME} 2-2"
RICHEST = f"{GROUP_NAME} 1-2"
STARS = {f"{GROUP_NAME} 1-1": 100, RICHEST: 500, f"{GROUP_NAME} 2-1": 200,
         POOR_PANEL: 5}


async def test_a_refocus_due_on_a_poor_panel_waits_and_the_hop_places_it(
        group_hub, group_store, monkeypatch):
    """Panel 2-2 is hopped to unmeasured, sweeps there (the first visit), and
    its frames count 5 stars. From then on a refocus due between its frames
    is not taken on it, and each later hop to it sweeps on 1-2.

    MUTATION "the frame loop refocuses a poor panel" (`_refocus_left_to_the_
    hop` returns False). Observed:
        AssertionError: sweeps on the poor panel: ['M31 2-2', 'M31 2-2',
        'M31 2-2', 'M31 2-2']; only the first visit's, taken before its stars
        were known, belongs there"""
    plan = grid_plan(autofocus_every=1)
    for t in plan.targets:
        t.autofocus_first = True
        t.autofocus_skip_if_fresh = True
    night = Night(group_hub, monkeypatch,
                  stars=lambda target, filt: STARS[target])
    night.hub.devices["focuser"] = _Focuser()
    sweeps: list[tuple[str, str]] = []
    # Where the mount was aimed last, by panel: the harness names a goto by
    # the panel being visited, and a sweep placed elsewhere is aimed there.
    at = {(t.ra_hours, t.dec_deg): t.name for t in plan.targets}
    aimed: list[str] = []
    real_goto = night.hub.goto_and_center

    async def goto_and_center(ra_hours, dec_deg, *args, **kw):
        aimed.append(at[(ra_hours, dec_deg)])
        return await real_goto(ra_hours, dec_deg, *args, **kw)

    monkeypatch.setattr(night.hub, "goto_and_center", goto_and_center)

    async def run_autofocus(cam, foc, **kw):
        sweeps.append((aimed[-1], str(night.engine.state.get("target", ""))))
        return AutofocusResult(True, 30000, 2.0, [], "ok")

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]

    on_poor = [s for s in sweeps if s[0] == POOR_PANEL]
    assert len(on_poor) == 1, (
        f"sweeps on the poor panel: {[s[0] for s in on_poor]}; only the first "
        f"visit's, taken before its stars were known, belongs there")
    assert (RICHEST, POOR_PANEL) in sweeps, (
        f"no hop to the poor panel had its sweep placed on the richest: "
        f"{sweeps}")
    assert night.said("a refocus that falls due here is left for the next hop")
    shots = [f for t, f in night.shots() if t == POOR_PANEL]
    assert shots == ["L", "R"] * 3, (
        f"the poor panel was not shot to its end: {shots}")
    assert not night.said(f"{POOR_PANEL}: skipped"), night.lines[-6:]
