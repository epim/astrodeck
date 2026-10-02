# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A safety pause that a panel's own pre-slew gate opened closes out fully,
even when the floor re-check that ends it refuses the hop (item 11; S3
orchestrator ruling 1, spec 5.1's reach-wait row and 5.8).

THE DEFECT. The monitor half of `_setup_target`'s pre-slew gate can open a
safety pause for the panel it is about to slew to (`_on_unsafe`, then
`_park_hold_pause`). When the sky reads safe again the pause re-checks the
mount's floor for that panel BEFORE it closes out, and since S3 a floor or
keep-out refusal at a panel's own hop is a reach wait, not the end of the
run: `_visit_panel` catches the `SlewRefused` and the run goes on with the
other panels. So the pause's close-out was skipped and never came back: no
"conditions safe again" line, no "resume" in the report, no safe verdict
published (the operator's screens went on reading UNSAFE for the rest of the
night), ``_unsafe_streak`` left where the trip put it, and no cooler gate
before the next panel's frames. The roof reopen's release (`_await_safe_and_
reopen`) re-checked the floor after its own close-out, but had no cooler gate
at all, so a sensor that drifted while the roof was shut went unchecked.

NOW both close out first: the line, the report, the published verdict, the
streak and the cooler gate, then the floor re-check, whose refusal is the
reach wait it always was.

SINCE #448 (S4 review item 9) the cooler gate comes FIRST within the pause's
close-out, reading the monitor through its wait, and the line, the report,
the verdict and the streak follow it only once it has passed; the reopen's
gate reads the monitor through its wait too. These cases are unchanged by
that, deliberately: the fixture plan asks for no temperature, so the gate
returns at once and the five still land at the one instant these cases pin.
The order when the gate does wait, and the rain it must see meanwhile, is
test_safe_again_after_cooler_gate.py's. The two engine mutants below were
re-run against the #448 shape (S5-ENG-SAFE-mut) and are still RED with the
failures quoted.

THE HARNESS is tests/_group_harness.py's clocked night, and the refusal is
test_group_preslew_floor_wait's: panel 2-2 is moved low in the east, under a
no-go wedge on the azimuths it crosses between ``T_GATE`` and ``T_CLEAR``,
so it is clear when it is picked and refused at ``T_GATE``. THE WEATHER is a
script on the fake clock (`_rain_at_the_hop`): safe until 2-2's first
pre-slew gate reads the monitor, rain from that moment until ``T_GATE``, and
safe after, so the pause opens at 2-2's hop and its first safe read lands
at the refusal. The fixture site is 40 N 74 W, not anybody's rig, and no
altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/ (scratchpad s4-enga-mut), never in the shared tree (#254).
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import Night, grid_plan, group_hub, group_store  # noqa: F401
from astrodeck import events
from astrodeck.config import SafetyConfig
from astrodeck.devices.base import DomeShutterState, SafetyReading
from astrodeck.sequence.report import SessionReporter
from test_group_preslew_floor_wait import (FAILING, OTHERS, T_CLEAR, T_GATE,
                                           _far, _gotos, _name, _run, _shot,
                                           _wedge_for)

#: The pause's re-read cadence: its first safe read lands within one of it.
POLL_S = engine_mod.SAFETY_PAUSE_POLL_S


def _plan(group_store, *, wedge: bool = True, roof: bool = False):
    """The fixture 2x2, 2-2 moved onto the wedge's path, the monitor armed:
    a trip pauses (or, with ``roof``, closes the roof and reopens it when
    safe), on one unsafe read, and one safe read ends it, with no bound.
    ``wedge`` False leaves the limits open, so the re-check passes."""
    plan = grid_plan()
    p22 = next(t for t in plan.targets if t.name == _name(FAILING))
    _far(p22)
    group_store.set_safety(SafetyConfig(
        enabled=True, on_unsafe="pause", unsafe_consecutive=1,
        resume_safe_consecutive=1, max_pause_min=0, sky_fallback_hold=False,
        close_dome_on_unsafe=roof, reopen_dome_when_safe=roof,
        nogo_box=[_wedge_for(p22)] if wedge else None))
    assert plan.safety_check, "premise: the plan honours the monitor"
    return plan


def _rain_at_the_hop(night, monkeypatch, *, until: float) -> dict:
    """The monitor's cached reading, a script on the fake clock: safe until
    2-2's first pre-slew gate asks for it, rain from that moment until
    ``until``, and safe after. Returns the record: when the rain began."""
    rain: dict = {"from": None}

    async def safety_reading():
        t = night.clock.t
        if rain["from"] is not None and t < until:
            return SafetyReading(is_safe=False, reason="rain sensor",
                                 source="script", ts=t)
        return SafetyReading(is_safe=True, source="script", ts=t)

    monkeypatch.setattr(night.hub, "safety_reading", safety_reading)
    spy = night.engine._safety_gate

    async def gate(*a, **kw):
        target = kw.get("target")
        if (rain["from"] is None and kw.get("context") == "slew"
                and target is not None and target.name == _name(FAILING)):
            rain["from"] = night.clock.t
        return await spy(*a, **kw)

    monkeypatch.setattr(night.engine, "_safety_gate", gate)
    return rain


def _watch(night, monkeypatch) -> dict:
    """Pass-through spies on the five things a close-out does, each stamped
    with the fake time: every bus line with its source (the pause's own
    lines are ``safety``'s, which `Night.lines` does not keep), every
    report safety event, every safety verdict handed to the hub to publish,
    every cooler gate, and every reach wait with ``_unsafe_streak`` as it
    stood when the refusal reached `_visit_panel`."""
    rec: dict = {"lines": [], "reports": [], "published": [], "coolers": [],
                 "refused": []}
    real_log = events.bus.log

    def log(level, message, source="hub", **kw):
        rec["lines"].append((night.clock.t, level, message, source))
        return real_log(level, message, source, **kw)

    monkeypatch.setattr(events.bus, "log", log)
    real_record = SessionReporter.record_safety

    def record_safety(self, reason, action):
        rec["reports"].append((night.clock.t, reason, action))
        return real_record(self, reason, action)

    monkeypatch.setattr(SessionReporter, "record_safety", record_safety)
    real_publish = night.hub.publish_safety

    def publish_safety(payload):
        rec["published"].append((night.clock.t, dict(payload)))
        return real_publish(payload)

    monkeypatch.setattr(night.hub, "publish_safety", publish_safety)
    real_cooler = night.engine._cooler_gate

    async def cooler_gate(why, **kw):
        rec["coolers"].append((night.clock.t, why))
        return await real_cooler(why, **kw)

    monkeypatch.setattr(night.engine, "_cooler_gate", cooler_gate)
    real_refused = night.engine._hop_refused_by_a_limit

    def refused(group, target, refusal, remaining):
        rec["refused"].append((night.clock.t, target.name,
                               night.engine._unsafe_streak))
        return real_refused(group, target, refusal, remaining)

    monkeypatch.setattr(night.engine, "_hop_refused_by_a_limit", refused)
    return rec


def _close_out(rec: dict, t: float, *, line: str, report: tuple[str, str],
               action: str, cooler: str, streak: int | None) -> list[str]:
    """Which of the five a close-out does did NOT happen at fake time ``t``:
    the line, the report event, the safe verdict published, the streak at
    0 (``streak`` as observed; None when the case has no refusal to observe
    it at), and the cooler gate."""
    checks = {
        f"the {line!r} line": any(
            at == t and m == line and src == "safety"
            for at, _lvl, m, src in rec["lines"]),
        f"record_safety{report!r}": (t, *report) in rec["reports"],
        "publish_safety is_safe True": any(
            at == t and p.get("is_safe") is True and p.get("action") == action
            for at, p in rec["published"]),
        "_unsafe_streak reset to 0": streak in (0, None),
        f"the cooler gate ({cooler})": (t, cooler) in rec["coolers"],
    }
    return [name for name, ok in checks.items() if not ok]


# ------------------------------------------------------------- the pause

async def test_a_pause_whose_floor_re_check_refuses_the_hop_still_closes_out(
        group_hub, group_store, monkeypatch):
    """Rain at 2-2's hop: its pre-slew gate pauses, and the pause lasts until
    ``T_GATE``, by when 2-2 has risen into the wedge. At the first safe read
    the pause closes out, all five at that instant: the "conditions safe
    again" line, the report's resume, the safe verdict published, the streak
    at 0 when the refusal reaches the scheduler, and the cooler gate. Then
    the floor re-check refuses 2-2, a reach wait: the run goes on with the
    other three, 2-2 is shot once past the wedge, and the night completes.

    Mutant "floor re-check before the close-out" (the order before item 11:
    `_park_hold_pause`'s ``_enforce_mount_floor`` moved back ahead of the
    line): RED (observed) -
        AssertionError: the pause 2-2's own gate opened was left open when
        its floor re-check refused the hop: missing ["the 'conditions safe
        again — resuming' line", "record_safety('conditions safe again',
        'resume')", 'publish_safety is_safe True', '_unsafe_streak reset to
        0', 'the cooler gate (resumed after a safety pause)']
    """
    plan = _plan(group_store)
    night = Night(group_hub, monkeypatch)
    rain = _rain_at_the_hop(night, monkeypatch, until=T_GATE)
    rec = _watch(night, monkeypatch)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert rain["from"] is not None and rain["from"] < T_GATE - 600.0, (
        f"premise: the rain began at 2-2's first hop, early: {rain}")
    paused = [t for t, _l, m, s in rec["lines"]
              if m == "UNSAFE: rain sensor → pause" and s == "safety"]
    assert paused == [rain["from"]], (
        f"premise: the gate paused once, at the hop: {paused}")
    refused = rec["refused"]
    assert [who for _t, who, _s in refused][:1] == [_name(FAILING)], (
        f"premise: 2-2's hop was refused, a reach wait: {refused}")
    t_ref = refused[0][0]
    assert T_GATE <= t_ref < T_GATE + POLL_S, (
        f"premise: the refusal came at the pause's first safe read: "
        f"{night.rel(t_ref)}")
    missing = _close_out(rec, t_ref, line="conditions safe again — resuming",
                         report=("conditions safe again", "resume"),
                         action="resume", cooler="resumed after a safety pause",
                         streak=refused[0][2])
    assert missing == [], (
        f"the pause 2-2's own gate opened was left open when its floor "
        f"re-check refused the hop: missing {missing}")
    words = (f"M31: {FAILING} cannot be slewed to now: {_name(FAILING)} would "
             f"be below the mount's altitude floor by the end of a slew "
             f"there; it waits for the limit to clear, and the refused hop "
             f"is not counted as a visit")
    assert night.said(words) == [words], night.lines[-6:]
    hops = _gotos(night, FAILING)
    assert hops and hops[0] >= T_CLEAR - night.t0, hops
    assert _shot(night, FAILING) == ["L", "R"] * 3, night.shots()
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"))


async def test_control_a_pause_whose_re_check_passes_is_unchanged(
        group_hub, group_store, monkeypatch):
    """CONTROL. The same rain at 2-2's hop, with no wedge, so the re-check at
    the first safe read passes. The pause closes out as it always did, all
    five at that instant, and returns to the setup it interrupted, which
    makes the acquisition once (#241): 2-2 is slewed to at the release and
    shot, nothing waits, and the night completes. The reordering changes
    nothing here: it passed on the code before item 11 as well.
    """
    plan = _plan(group_store, wedge=False)
    night = Night(group_hub, monkeypatch)
    rain = _rain_at_the_hop(night, monkeypatch, until=T_GATE)
    rec = _watch(night, monkeypatch)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert rain["from"] is not None and rain["from"] < T_GATE - 600.0, rain
    safe = [t for t, _l, m, s in rec["lines"]
            if m == "conditions safe again — resuming" and s == "safety"]
    assert len(safe) == 1 and T_GATE <= safe[0] < T_GATE + POLL_S, (
        f"premise: the pause released at its first safe read: "
        f"{[night.rel(t) for t in safe]}")
    missing = _close_out(rec, safe[0], line="conditions safe again — resuming",
                         report=("conditions safe again", "resume"),
                         action="resume", cooler="resumed after a safety pause",
                         streak=None)
    assert missing == [], missing
    assert night.engine._unsafe_streak == 0
    assert rec["refused"] == [], rec["refused"]
    assert night.said("through the acquisition the pause interrupted"), (
        night.lines[-6:])
    hops = _gotos(night, FAILING)
    assert hops and hops[0] == night.rel(safe[0]), (
        f"2-2 was slewed to at {hops}, not at the release "
        f"{night.rel(safe[0])}")
    assert _shot(night, FAILING) == ["L", "R"] * 3, night.shots()
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"))


# ------------------------------------------------------ the roof reopen

async def test_a_roof_reopen_whose_floor_re_check_refuses_the_hop_closes_out(
        group_hub, group_store, monkeypatch):
    """The same rain at 2-2's hop on a rig that closes the roof on unsafe
    and reopens it when safe. The gate closes the roof over the parked
    mount; at ``T_GATE`` the roof reopens, and the reopen closes out, all
    five at that instant (its own line and report event, the safe verdict
    published with ``reopen``, the streak at 0, and the cooler gate, which
    the reopen never ran before item 11) before the floor re-check refuses
    2-2 as a reach wait. The run goes on and completes, the roof open.

    Mutant "no cooler gate at the reopen" (the new ``_cooler_gate`` call in
    `_await_safe_and_reopen` deleted, as the reopen was before item 11): RED
    (observed) -
        AssertionError: the roof reopen 2-2's own gate opened was left open
        when its floor re-check refused the hop: missing ['the cooler gate
        (resumed after a roof reopen)']
    Mutant "floor re-check before the close-out" (the reopen's
    ``_enforce_mount_floor`` moved to the top of its safe-again branch,
    ahead of the streak reset and the line): RED (observed) -
        AssertionError: the roof reopen 2-2's own gate opened was left open
        when its floor re-check refused the hop: missing ["the 'conditions
        safe again — reopening roof' line", "record_safety('roof reopened —
        conditions safe again', 'reopen_roof')", 'publish_safety is_safe
        True', '_unsafe_streak reset to 0', 'the cooler gate (resumed after
        a roof reopen)']
    """
    dome = group_hub.devices.get("dome")
    assert dome is not None and dome.connected, "premise: the sim has a roof"
    plan = _plan(group_store, roof=True)
    night = Night(group_hub, monkeypatch)
    rain = _rain_at_the_hop(night, monkeypatch, until=T_GATE)
    rec = _watch(night, monkeypatch)
    await _run(night, plan)
    assert night.done, night.lines[-4:]
    assert rain["from"] is not None and rain["from"] < T_GATE - 600.0, rain
    closed = [t for t, reason, action in rec["reports"]
              if action == "close_roof"]
    assert closed == [rain["from"]], (
        f"premise: the gate closed the roof at the hop: {rec['reports']}")
    refused = rec["refused"]
    assert [who for _t, who, _s in refused][:1] == [_name(FAILING)], (
        f"premise: 2-2's hop was refused, a reach wait: {refused}")
    t_ref = refused[0][0]
    assert T_GATE <= t_ref < T_GATE + POLL_S, night.rel(t_ref)
    missing = _close_out(rec, t_ref,
                         line="conditions safe again — reopening roof",
                         report=("roof reopened — conditions safe again",
                                 "reopen_roof"),
                         action="reopen", cooler="resumed after a roof reopen",
                         streak=refused[0][2])
    assert missing == [], (
        f"the roof reopen 2-2's own gate opened was left open when its floor "
        f"re-check refused the hop: missing {missing}")
    assert _shot(night, FAILING) == ["L", "R"] * 3, night.shots()
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "complete"), (last["state"], last.get("end_reason"))
    assert await dome.shutter_state() is DomeShutterState.OPEN
