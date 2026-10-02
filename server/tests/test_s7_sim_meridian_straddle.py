# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A rotating 2x2 whose panels straddle the meridian, end to end (#189 S7
item 1, the last of its four simulator scenarios; spec 5.7, 5.10, 6.9,
6.11; #422).

The flow is the S5 probe's 2x2 at Rotate to PA 55 (tests/_flow_night.py),
its centre 24 min of hour angle east of the meridian at the harness's
``T0`` (``ra_at(-0.4)``), with ``L 10, R 10`` for twenty cycles: enough
frames to shoot before the meridian, wait, and shoot after it. Meridian
flips are on (the plan's default, which a flow does not change), with the
plan's 10 min lead. The panels span 2.3 min of hour angle, so when the
first crosses the others have not: the group runs its pre-flip visits
until every panel runs out of room before its flip point, waits for the
first crossing (spec 5.7, "Cost 1, the pre-flip idle"), changes pier side
once at the hop that ends the wait, and from then on shoots only panels
past the meridian, as they cross ("Cost 2").

THE CHIP DURING THE WAIT IS THE SIMULATOR'S. The engine park-holds the
mount at the wait's start ("stopping tracking"), and the simulator keeps
its RA with tracking off (#519), so the hub's meridian block goes on
counting down to 2-2's crossing for a mount that is not moving, and the
chip is published from it. The checks below read the chip against where
the mount points, as tests/test_s7_meridian_chip_cleared.py does, so they
hold either way; the countdown's values during the wait are not graded.

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-E2E-mut``), from a byte backup, never in the shared tree; each failure
is quoted where it was observed.
"""
from __future__ import annotations

import astrodeck.catalog.coords as coords_mod
from _flow_night import (FlowRig, assert_the_three_agree, flow_rig,  # noqa: F401
                         mosaic_flow, panel_label, shot_counts, steps_by_panel)
from _group_harness import LON, group_store, ra_at  # noqa: F401

FLOW = mosaic_flow(ra_hours=ra_at(-0.4), plan="L 10, R 10", cycles=20)
#: A clock time inside the meridian wait, which runs from the moment the
#: last panel runs out of room (720 s as built) to the first crossing plus
#: the side margin (1384 s): a fixed instant well inside both ends.
IN_THE_WAIT_S = 1000.0


def _hour_angle(ra: float, t: float) -> float:
    return coords_mod.hour_angle_h(ra, LON, t)


def _hours_to_flip(ra: float, t: float) -> float:
    """What the hub's meridian block says for a mount pointing at ``ra`` at
    ``t``, to its four places (`Hub._compute_meridian`): minus the hour
    angle. The ``live`` chip comes from this block."""
    return round(-_hour_angle(ra, t), 4)


async def test_a_straddling_2x2_waits_for_the_meridian_and_changes_pier_side_once(
        flow_rig):
    """The group holds for the flip point and publishes ``meridian_wait``
    (an operator is served the panel and pass; a viewer is not, spec 5.10,
    6.9), makes exactly one pier change that night, at the hop that ends the
    wait, with the panel centres straddling the meridian there, and shoots
    no frame across the pier: every frame on the pre-flip side ends before
    its panel's crossing, and every frame on the other side starts after
    it. No state published with the mount past its crossing carries
    ``live.meridian_eta_s`` (#422), while the states inside the warn window
    before it do (the control). The ledger, the report and the progress
    route agree, and the session completes.

    RED under mutant "second pier change allowed" (``group_rules.
    meridian_eligibility`` letting every panel through once the group has
    flipped, the panels before the meridian included: its flipped branch's
    ``if h_p <= -crossed`` made ``if True``), observed:

        AssertionError: at +2308.6 s of night 1: the pier changes that
        night were [(1384.079, 'west', 'east'), (1404.079, 'east', 'west'),
        (1448.59, 'west', 'east')]
          Left contains 2 more items, first extra item: (1404.079, 'east',
          'west')

    (the hop back to a panel before the meridian lands on the old side,
    and the group's side check then defers it, so no frame is shot there:
    only the slews show the second and third changes).

    RED under mutant "chip merged past the crossing" (``SequenceEngine.
    _set_state`` without #422's clear, ``elif "live" not in kw:
    self.state.pop("live", None)``, so ``live`` is only ever merged),
    observed:

        AssertionError: at +2264.1 s of night 1: 220 states published past
        the crossing carried the meridian chip, the first: [(1384.079,
        {'meridian_eta_s': 129}), (1394.079, {'meridian_eta_s': 129}),
        (1394.079, {'meridian_eta_s': 129})]

    RED under mutant "viewer detail withheld" (``api.redact.
    _withhold_group_timing`` dropping the state's ``detail`` too across the
    wait), the check that the viewer's state is the operator's outside the
    group and the chip, observed:

        AssertionError: at +1000.0 s of night 1: the viewer's state differs
        outside the group and the live chip, which today's redaction does
        not do (spec 6.9, 5.10)
          Left contains 1 more item:
          {'detail': 'M31: waiting for the meridian'}

    RED under mutant "live rejoins the derived table dropped" (``_DERIVED_
    NODES`` in api/redact.py put back to its two original entries, ``mount``
    and ``meridian``, #166 item 1): RED (observed):

        AssertionError: at +1000.0 s of night 1: a viewer read the meridian
        countdown: {'meridian_eta_s': 795}
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    assert plan.meridian_flip and plan.meridian_flip_lead_min == 10.0, (
        "premise, before the night: meridian flips are on, with the "
        "plan's 10 min lead")
    ids = steps_by_panel(plan)
    ra = {panel_label(t.name): t.ra_hours for t in plan.targets}
    tel = night.hub.devices["telescope"]

    after_the_flip: list[dict] = []

    def on_capture(rec: dict) -> None:
        # The side every frame is shot on, off the simulator's own latch.
        rec["side"] = tel._latched_side.value
        if (rec["side"] == "east" and not after_the_flip):
            after_the_flip.append(rec)
            night.hold(rec["t"])

    night.on_capture = on_capture
    night.hold(night.t0 + IN_THE_WAIT_S)
    r = await rig.run(fid)
    assert r.status_code == 200, f"at +0.0 s of night 1: {r.text}"

    assert await night.settle(), (
        f"premise, {rig.at(night)}: the night reached the wait")
    wait = await rig.read(night, fid)
    at = wait.at
    group, seen = wait.state["group"], wait.viewer["group"]
    assert (group["meridian_wait"], wait.state["detail"]) == (
        True, "M31: waiting for the meridian"), (
        f"{at}: the state reads {wait.state['detail']!r}, group {group}")
    assert group.get("panel") and group.get("pass"), (
        f"{at}: an operator is served the panel and the pass: {group}")
    assert "panel" not in seen and "pass" not in seen and {
        k: v for k, v in group.items() if k not in ("panel", "pass")} == seen, (
        f"{at}: a viewer is served {seen} across the meridian wait")
    # ``live.meridian_eta_s`` IS #166 ITEM 1's: the same countdown the status
    # redaction strips as ``meridian.hours_to_flip`` (api/redact.py), republished
    # unredacted a level up because the sequence node was never in
    # ``_DERIVED_NODES``. The operator reads it (the premise below); a viewer
    # must not, though it may still carry other, non-site chip fields
    # (``sensor_temp_c``), so the check is on the one key, not on absence of
    # the whole block.
    assert (wait.state.get("live") or {}).get("meridian_eta_s"), (
        f"{at}: premise, a countdown to grade the redaction against: "
        f"{wait.state.get('live')}")
    assert "meridian_eta_s" not in (wait.viewer.get("live") or {}), (
        f"{at}: a viewer read the meridian countdown: {wait.viewer.get('live')}")
    assert {k: v for k, v in wait.state.items() if k not in ("group", "live")
            } == {k: v for k, v in wait.viewer.items()
                  if k not in ("group", "live")}, (
        f"{at}: the viewer's state differs outside the group and the live "
        f"chip, which today's redaction does not do (spec 6.9, 5.10)")
    assert_the_three_agree(wait, plan)

    night.release()
    assert await night.settle(), (
        f"premise, {rig.at(night)}: the night shot past the meridian")
    flipped = await rig.read(night, fid)
    at = flipped.at
    rec = after_the_flip[0]
    assert flipped.state["group"]["meridian_wait"] is True and \
        flipped.state["group"]["panel"] == panel_label(rec["target"]), (
        f"{at}: the first exposure after the wait: {flipped.state['group']}")
    assert "panel" not in flipped.viewer["group"], (
        f"{at}: the hop that ends the wait is announced to a viewer by its "
        f"panel: {flipped.viewer['group']}")
    assert_the_three_agree(flipped, plan)

    night.on_capture = lambda rec: rec.update(side=tel._latched_side.value)
    await night.finish(wall_s=120.0)
    end = await rig.read(night, fid)
    at = end.at

    slews = night.pier_slews
    changes = [(night.rel(u), a, b)
               for (_t, a), (u, b) in zip(slews, slews[1:]) if a != b]
    assert slews[0][1] == "west" and changes == [
        (night.rel(rec["t"]), "west", "east")], (
        f"{at}: the pier changes that night were {changes}")
    t_flip = rec["t"]
    hour_angles = {p: _hour_angle(ra[p], t_flip) for p in ra}
    assert min(hour_angles.values()) < 0 < max(hour_angles.values()), (
        f"{at}: the panel centres do not straddle the meridian at the pier "
        f"change: {hour_angles}")
    waits = [night.rel(p["t"]) for p in night.published
             if (p["group"] or {}).get("meridian_wait")]
    assert waits and waits[0] < IN_THE_WAIT_S < night.rel(t_flip), (
        f"{at}: the meridian wait was published at {waits[:3]}...")

    across = []
    for c in night.captures:
        p = panel_label(c["target"])
        start, stop = c["t"], c["t"] + c["exposure_s"]
        if c["side"] == "west" and _hour_angle(ra[p], stop) >= 0:
            across.append((night.rel(start), p, "west past its crossing"))
        if c["side"] == "east" and _hour_angle(ra[p], start) < 0:
            across.append((night.rel(start), p, "east before its crossing"))
    assert across == [], f"{at}: frames across the pier: {across}"

    past = [(night.rel(p["t"]), p["live"]) for p in night.published
            if _hours_to_flip(p["ra"], p["t"]) <= 0 and p["live"] is not None
            and "meridian_eta_s" in p["live"]]
    assert past == [], (
        f"{at}: {len(past)} states published past the crossing carried the "
        f"meridian chip, the first: {past[:3]}")
    counted = [p for p in night.published
               if (p["live"] or {}).get("meridian_eta_s")]
    assert counted and all(_hours_to_flip(p["ra"], p["t"]) > 0
                           for p in counted), (
        f"control, {at}: the countdown was published before the crossing")

    shots = shot_counts(night.shots())
    assert shots == {k: 20 for k in ids}, f"{at}: shots {shots}"
    assert end.ledger() == {ids[k]: n for k, n in shots.items()}, (
        f"{at}: each step's ledger count is not Night.shots for its panel "
        f"and filter")
    assert_the_three_agree(end, plan)
    assert end.progress["session"]["status"] == "complete", (
        f"{at}: the session ends {end.progress['session']}")
