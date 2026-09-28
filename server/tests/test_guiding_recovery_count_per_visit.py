"""The #72 guiding-recovery count is the HOP'S, not the engine's (#329; spec
5.6 step 7).

THE DEFECT. `SequenceEngine._guiding_recoveries` counts the #72 recovery
attempts, and the bound acts when it reaches ``_MAX_GUIDING_RECOVERIES``
(2). It was one count for the whole engine, cleared only by a banked frame
and by a ``guide_lost`` deferral. A visit that ended by its own bound, with
attempts spent and no frame banked, handed them to the next hop: in a
mosaic that panel was deferred as ``guide_lost`` on its first loss, having
made no attempt of its own, under the sentence "guiding could not be kept
after 2 recovery attempts without a frame" (#329's probe, on this harness);
and a single target under ``guiding_action`` abort ended the night on its
first loss.

THE FIX. `_hop`: once a hop's own guider start works (a member's visit in
`_visit_panel`, a follower's visit, a single target's run), the count starts
again from 0. Not at every start: a cloud hold's release re-runs
`_setup_target` inside the visit, and a reset there would re-arm the bound
on every cycle of a hold the trailed frames keep opening. Not after a start
that failed either: that hop keeps the count it came with. The banked-frame
reset stays inside the hop, which is where #134 (open) still leaves the
bound unreachable in attempts mode, so every case here runs in accepted
mode, where a trailed frame is rejected and banks nothing.

THE SKY, AS SCRIPTED. The guider starts when asked (unless a case scripts
otherwise), and the star is lost during every exposure of the targets named
(the harness's ``on_capture`` hook marks the guider inactive before the
shutter closes). A frame reports the plan's ``min_stars`` worth of stars only
while the guider is guiding, so a frame shot with the star lost is trailed
and the real grader rejects it. Each recovery re-centres and starts the
guider again, and the star is lost again in the next exposure: the shape
recovery took on 2026-09-20 (#72).

Every case runs the real scheduler, hop, frame loop, recovery and grader on
the clocked simulator (tests/_group_harness.py). Each names the mutant it
was shown RED under, with the failure observed, verbatim (long lines
wrapped). Every mutant was applied in a private scratch copy of server/
(scratchpad/s4-engc-mut), never in the shared tree (#254), and run again
on the finished S4 tree on 2026-09-27 (scratchpad/s4-engc-resume-mut),
where each failed as recorded.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.config import EscalationConfig
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import session_store

#: What the bound says when it gives up (`_maybe_recover_guiding`).
GAVE_UP = ("guiding could not be kept after 2 recovery attempts without a "
           "frame")
#: What a recovery attempt says, numbered ``(k/2)``.
ATTEMPT = "guiding lost \u2014 attempting recovery"
#: The warn escalation's stand-down.
STOOD_DOWN = "standing down from recovery and continuing unguided"


def _plan_kw(**over) -> dict:
    """Guiding with recovery on, accepted mode with a star floor the real
    grader enforces, and the night reject guard off."""
    kw = dict(guide=True, recover_guiding=True, count_mode="accepted",
              min_stars=5, max_consecutive_rejects=20,
              max_consecutive_rejects_night=0)
    kw.update(over)
    return kw


async def _lost_night(hub, monkeypatch, plan, *, lose: set[str], guide=None,
                      before_run=None) -> Night:
    """Run ``plan`` with the star lost during every exposure of each target
    named in ``lose``, and a frame graded trailed (no stars) while the star
    is lost. ``guide(target, attempt)`` scripts the guider's starts."""
    guider = hub.guider

    def on_capture(rec):
        if rec["target"] in lose:
            guider.active = False

    night = Night(hub, monkeypatch, guide=guide,
                  stars=lambda who, f: 50 if guider.active else 0)
    night.on_capture = on_capture
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _visits(night: Night, who: str) -> list[str]:
    """``who``'s visits, one string each, from the trace: "S" a guider start
    (the hop's, a recovery's, a release's), "R" a recovery attempt, "H" a
    stood-in hold's release, "C" an exposure, "D" a guide-lost deferral of
    ``who`` (the set-aside after the last of a streak included). A visit
    begins at a hop, which is a goto neither a recovery nor a release made:
    each of those says so first, and then re-centres with a goto of its
    own."""
    label = who[len(GROUP_NAME) + 1:] if who.startswith(GROUP_NAME) else who
    out: list[list[str]] = []
    current = None
    recentring = False
    for entry in night.trace:
        kind = entry[1]
        if kind == "log" and ATTEMPT in entry[3]:
            recentring = True
            if current == who:
                out[-1].append("R")
        elif kind == "stand-in":
            recentring = True
            if current == who:
                out[-1].append("H")
        elif kind == "log" and f"did not recover on {label} " in (
                entry[3].replace(":", " ")):
            out[-1].append("D")
        elif kind == "goto":
            if recentring:
                recentring = False
                continue
            current = entry[2]
            if current == who:
                out.append([])
        elif kind == "guider" and entry[2] == "start" and entry[3][0] == who:
            if out and current == who:
                out[-1].append("S")
        elif kind == "capture" and entry[2] == who and current == who:
            out[-1].append("C")
    return ["".join(v) for v in out]


def _attempts(night: Night) -> list[str]:
    """Every recovery attempt's number, ``"1/2"``, in order."""
    return [m[len(ATTEMPT) + 2:-1] for _t, _lvl, m in night.lines
            if m.startswith(ATTEMPT)]


# ------------------------------------------------------------ the mosaic

async def test_each_guide_lost_deferral_follows_two_attempts_on_its_own_visit(
        group_hub, group_store, monkeypatch):
    """#329's probe shape: a 1x2 of one filter whose panels both lose the
    star in every exposure, guiding optional and ``guiding_action`` warn.
    1-2's visit is 3 frames: its hop's frame and one after each of its two
    recovery attempts, so every visit of 1-2 ends by its own bound with its
    attempts spent and no frame banked, before the recovery bound can act.
    1-1's visit is 4 frames, so the bound acts inside it, before its fourth.

    Each of 1-1's three visits makes its hop's start and two recovery
    attempts of its own, numbered (1/2) and (2/2) on that visit, each with
    its frame, and only then is deferred as ``guide_lost``, with a sentence
    ("after 2 recovery attempts") that is true of that visit; after the
    third 1-1 is set aside. 1-2 is never deferred.

    MUTANT "engine-wide count" (`_hop`'s reset deleted, S3's count): 1-2's
    two spent attempts reach 1-1's next visit, which is deferred on its
    first loss, having made no attempt of its own. RED (observed):
        AssertionError: 1-1's visits: ['SCRSCRSCD', 'SCD', 'SCD']
        assert ['SCRSCRSCD', 'SCD', 'SCD'] == ['SCRSCRSCD',...,
        'SCRSCRSCD']
          At index 1 diff: 'SCD' != 'SCRSCRSCD'
    (The deferral's own reset of the count, which S3 made, stays: with it
    deleted and `_hop`'s kept, every case in this file and in
    test_group_guide_lost_defers.py stays green, since the next hop's start
    resets the count anyway.)
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"filters": ("L",), "count": 12,
                               "per_visit": 4}, **_plan_kw())
    plan.targets[1].steps[0].per_visit = 3
    one_one, one_two = f"{GROUP_NAME} 1-1", f"{GROUP_NAME} 1-2"
    night = await _lost_night(group_hub, monkeypatch, plan,
                              lose={one_one, one_two})
    assert night.done, night.trace[-3:]
    v11, v12 = _visits(night, one_one), _visits(night, one_two)
    assert v11 == ["SCRSCRSCD"] * 3, f"1-1's visits: {v11}"
    assert v12 and set(v12[:3]) == {"SCRSCRSC"}, (
        f"premise: 1-2's visits end by their own bound with both attempts "
        f"spent: {v12}")
    deferred = night.said(f"guiding was lost and did not recover on 1-1: "
                          f"{GAVE_UP}; retried on the next pass")
    assert len(deferred) == 2, night.said("did not recover")
    assert not night.said("did not recover on 1-2"), (
        night.said("did not recover"))
    # Every visit numbers its own attempts from 1.
    assert _attempts(night)[:12] == ["1/2", "2/2"] * 6, _attempts(night)
    aside = {r["target_id"]: r["reason"] for r in night.stored.set_aside}
    assert aside.get("p00") == (f"guiding was lost and did not recover on "
                                f"1-1 on 3 consecutive visits: {GAVE_UP}"), (
        night.stored.set_aside)


# -------------------------------------------------------- single targets

def _two_singles(**over) -> SequencePlan:
    """Alpha, then Bravo, one filter each, four frames, a step reject guard
    of 3 so a target whose every frame is trailed is set aside after its
    third."""
    return SequencePlan(name="two", dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False,
                        targets=[single("Alpha", count=4),
                                 single("Bravo", count=4, ha_h=-2.4)],
                        **_plan_kw(max_consecutive_rejects=3, **over))


async def test_a_single_targets_hop_starts_its_own_budget(
        group_hub, group_store, monkeypatch):
    """Alpha loses its star in every exposure and makes both its recovery
    attempts; its step's reject guard then sets it aside after its third
    trailed frame, with the attempts spent and no frame banked. Bravo, the
    next target, loses its star too. Guiding is required and
    ``guiding_action`` is abort. Bravo's hop starts its guider, and Bravo
    makes two recovery attempts of its own before anything could end the
    night; its reject guard sets it aside too, and the run ends incomplete,
    not unsafe.

    MUTANT "engine-wide count" (`_hop`'s reset deleted): Alpha's spent
    attempts reach Bravo, whose first loss ends the night with no attempt
    of its own. RED (observed):
        AssertionError: ('unsafe', 'guiding could not be kept after 2
        recovery attempts without a frame')
        assert 'unsafe' == 'incomplete'
          - incomplete
          + unsafe
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    night = await _lost_night(group_hub, monkeypatch, _two_singles(),
                              lose={"Alpha", "Bravo"})
    assert night.done, night.trace[-3:]
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended[0] == "incomplete", ended
    assert _visits(night, "Alpha") == ["SCRSCRSC"], _visits(night, "Alpha")
    assert _visits(night, "Bravo") == ["SCRSCRSC"], _visits(night, "Bravo")
    assert _attempts(night) == ["1/2", "2/2", "1/2", "2/2"], _attempts(night)


async def test_a_holds_release_mid_visit_does_not_rearm_the_bound(
        group_hub, group_store, monkeypatch):
    """A cloud hold's release re-acquires the target inside its run: it
    re-runs `_setup_target`, whose guider start works. That is not a hop,
    and it must not start the budget again: a hold the trailed frames of a
    lost star keep opening would otherwise re-arm the bound on every cycle.

    THE HOLD IS STOOD IN FOR at Solo's third frame gate: the release
    `_hold_for_clear` makes for a hold opened from the frame loop, which
    calls ``self._setup_target(self._index_of_target(target), target)``,
    made by the stand-in in the same words. A real hold needs a cloud
    verdict and a sky that clears, which is not what this is about.

    Solo, guiding required and ``guiding_action`` abort, loses its star in
    every exposure. Its hop's frame, attempt (1/2) and its frame, the
    release's start and its frame, then attempt (2/2) and its frame; at the
    next frame the bound is reached and the night ends, after four frames.

    MUTANT "the budget re-armed at every setup" (``self._guiding_recoveries
    = 0`` added beside ``self._hop_guide_started = True`` in `_setup_target`,
    so the release's start resets it too): the attempt after the release is
    numbered (1/2) again, and the night runs a frame longer. RED (observed):
        AssertionError: ['1/2', '1/2', '2/2']
        assert ['1/2', '1/2', '2/2'] == ['1/2', '2/2']
          At index 1 diff: '1/2' != '2/2'
          Left contains one more item: '2/2'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    plan = SequencePlan(name="solo", dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False,
                        targets=[single("Solo", count=8)], **_plan_kw())
    released: list[float] = []

    def stand_in(night):
        eng = night.engine
        spy = eng._safety_gate
        seen = [0]

        async def gate(*a, **kw):
            target = kw.get("target")
            if (kw.get("context") == "frame" and target is not None
                    and target.name == "Solo"):
                seen[0] += 1
                if seen[0] == 3:
                    released.append(night.rel(engine_mod.time.time()))
                    night._note("stand-in", "release")
                    await eng._setup_target(eng._index_of_target(target),
                                            target)
            return await spy(*a, **kw)

        monkeypatch.setattr(eng, "_safety_gate", gate)

    night = await _lost_night(group_hub, monkeypatch, plan, lose={"Solo"},
                              before_run=stand_in)
    assert night.done, night.trace[-3:]
    assert len(released) == 1, f"premise: the stand-in released once: {released}"
    assert _attempts(night) == ["1/2", "2/2"], _attempts(night)
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended == ("unsafe", GAVE_UP), ended
    assert _visits(night, "Solo") == ["SCRSCHSCRSC"], _visits(night, "Solo")


async def test_a_real_cloud_holds_release_does_not_rearm_the_bound(
        group_hub, group_store, monkeypatch):
    """The case above through the REAL `_hold_for_clear`, so its release
    site is graded too: the stand-in above re-runs `_setup_target` in the
    release's words, and cannot see the release itself call `_hop`. At
    Solo's third frame gate the real hold is opened, as the frame loop's
    sky verdict opens one, with the check frames scripted clear
    (`_cloud_probe` answering False, as test_cloud_hold_follows_the_mount.py
    scripts it): the hold stands the guider down, takes its two clear
    probes, and releases, re-acquiring Solo through its own setup, whose
    guider start works. Solo's attempts are still numbered (1/2), then
    (2/2), and the night ends at the bound after four frames.

    MUTANT "the hold's release is a hop" (the verifier's, run in
    scratchpad/s4-engc-verify-mut: `_hold_for_clear`'s release calling
    ``self._hop(...)`` in place of ``self._setup_target(...)``): the
    stand-in case above stays green (observed); here the release re-arms
    the bound. RED (observed):
        AssertionError: ['1/2', '1/2', '2/2']
        assert ['1/2', '1/2', '2/2'] == ['1/2', '2/2']
          At index 1 diff: '1/2' != '2/2'
          Left contains one more item: '2/2'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    plan = SequencePlan(name="solo", dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False,
                        targets=[single("Solo", count=8)], **_plan_kw())
    held: list[float] = []

    def open_a_real_hold(night):
        eng = night.engine
        spy = eng._safety_gate
        seen = [0]

        async def clear(*a, **kw):
            return False

        async def gate(*a, **kw):
            target = kw.get("target")
            if (kw.get("context") == "frame" and target is not None
                    and target.name == "Solo" and not held):
                seen[0] += 1
                if seen[0] == 3:
                    held.append(night.rel(engine_mod.time.time()))
                    night._note("stand-in", "hold")
                    await eng._hold_for_clear(
                        "no safety monitor is assigned and the frames say "
                        "the sky has closed in", target)
            return await spy(*a, **kw)

        monkeypatch.setattr(eng, "_cloud_probe", clear)
        monkeypatch.setattr(eng, "_safety_gate", gate)

    night = await _lost_night(group_hub, monkeypatch, plan, lose={"Solo"},
                              before_run=open_a_real_hold)
    assert night.done, night.trace[-3:]
    assert len(held) == 1, f"premise: the hold opened once: {held}"
    assert night.said("sky cleared after"), (
        "premise: the real hold released", night.said("cloud"))
    assert _attempts(night) == ["1/2", "2/2"], _attempts(night)
    # The hop's start, the two recoveries' and the release's, every one
    # working: the release's start is the one the bound must not count.
    starts = [e for e in night.trace if e[1] == "guider"
              and e[2] == "start" and e[3][0] == "Solo"]
    assert len(starts) == 4 and all(e[3][2] for e in starts), starts
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended == ("unsafe", GAVE_UP), ended
    assert len(night.captures) == 4, [c["target"] for c in night.captures]


async def test_a_hop_whose_start_failed_keeps_the_count_it_came_with(
        group_hub, group_store, monkeypatch):
    """Only a hop whose own start WORKED starts the budget again. Alpha, as
    above, spends both attempts; Bravo's hop start fails, and guiding is
    optional under warn, so Bravo is shot on unguided and keeps the count
    Alpha left: its first frame boundary finds the bound reached and stands
    recovery down, rather than cycling re-centres and restarts for a guider
    that has just failed to start at this pointing after failing to be kept
    at the last. (The stand-down's "2 recovery attempts" are then Alpha's:
    the count is the hop's only from a start that worked.)

    MUTANT "the budget reset at every hop" (`_hop`'s ``if
    self._hop_guide_started:`` made ``if True:``): Bravo, whose guider never
    came up at its hop, is handed a fresh budget and makes two more
    re-centres and restarts. RED (observed):
        AssertionError: ['SRSCRSCC']
        assert ['SRSCRSCC'] == ['SCCC']
          At index 0 diff: 'SRSCRSCC' != 'SCCC'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    night = await _lost_night(
        group_hub, monkeypatch, _two_singles(), lose={"Alpha", "Bravo"},
        guide=lambda who, n: not (who == "Bravo" and n == 1))
    assert night.done, night.trace[-3:]
    assert _visits(night, "Alpha") == ["SCRSCRSC"], _visits(night, "Alpha")
    assert _visits(night, "Bravo") == ["SCCC"], _visits(night, "Bravo")
    assert _attempts(night) == ["1/2", "2/2"], _attempts(night)
    assert night.said(f"{GAVE_UP}; {STOOD_DOWN}"), night.lines[-6:]


# ------------------------------------------------------------- a follower

async def test_a_followers_visit_is_a_hop_and_starts_its_own_budget(
        group_hub, group_store, monkeypatch):
    """A follower's visit is a hop too (`_visit_follower` calls `_hop`). A
    1x2 whose panels never centre defers both on every pass, so each pass
    ends in a deferral wait, and a follower, a plain target after the
    mosaic, fills each wait in one visit bounded by its end
    (test_group_follower_fills_defer_wait.py); after the third pass both
    panels are set aside and the follower runs on as a plain target. The
    follower loses its star in every exposure, guiding optional under warn,
    and banks nothing (accepted mode, every frame trailed). A panel's hop
    never reaches its guider start, so nothing between the follower's
    visits resets the count.

    Each of the follower's two bounded visits, and its run after the
    mosaic, makes two recovery attempts of its own, numbered (1/2) and
    (2/2), before it stands recovery down.

    MUTANT "the follower's visit not a hop" (the verifier's, run in
    scratchpad/s4-engc-verify-mut: `_visit_follower` calling `_setup_target`
    in place of `_hop`): every other case in this file and in
    test_group_guide_lost_defers.py stays green (observed, 11 passed; 12 on
    the 2026-09-27 re-run, which has the real hold's case too); here
    the second visit inherits the first one's spent attempts and stands down
    at its first loss. RED (observed):
        AssertionError: ['1/2', '2/2', '1/2', '2/2']
        assert ['1/2', '2/2', '1/2', '2/2'] == ['1/2', '2/2'... '1/2',
        '2/2']
          Right contains 2 more items, first extra item: '1/2'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    follower = "Follower"

    def never_centres(who, n, result):
        if who.startswith(GROUP_NAME + " "):
            return dict(result, centered=False, error_arcmin=None)
        return result

    plan = grid_plan(rows=1, cols=2,
                     after=[single(follower, count=40)],
                     **_plan_kw(max_consecutive_rejects=30))
    guider = group_hub.guider

    def on_capture(rec):
        if rec["target"] == follower:
            guider.active = False

    night = Night(group_hub, monkeypatch, goto=never_centres,
                  stars=lambda who, f: 50 if guider.active else 0)
    night.on_capture = on_capture
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    assert len(night.said(f"every panel waits; shooting {follower} until "
                          f"the next panel is due")) == 2, (
        "premise: the follower filled two deferral waits",
        night.said("waits"))
    assert not [e for e in night.trace if e[1] == "guider"
                and e[2] == "start" and e[3][0].startswith(GROUP_NAME)], (
        "premise: no panel's hop reached its guider start")
    assert _attempts(night) == ["1/2", "2/2"] * 3, _attempts(night)
    assert len(night.said(f"{GAVE_UP}; {STOOD_DOWN}")) >= 3, (
        night.said("standing down"))
