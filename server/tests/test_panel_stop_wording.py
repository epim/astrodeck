"""A gate that stops a mosaic panel says what happens to the panel: the visit
stops and the panel is retried on the next pass (#326; spec 5.1's deferral
row, 5.8). And a hop the gate refused before any slew is no visit to the
panel order either (#326, "also noted").

THE DEFECT. Since #316 a plain ``StopTarget`` raised in a panel's visit
defers the panel (`group_rules.TARGET_STOP`), and it is set aside tonight
only after ``max_failed_visits`` consecutive failed visits. The gates that
raise it still said the single target's outcome for a panel:

* the cloud hold's floor check (`_hold_watch`): "setting it aside for the
  rest of this run";
* the hold's published end (`_hold_for_clear`): "cloud hold ended: <name>
  was set aside";
* the #72 recovery bound under skip, and the hop's guide start under skip,
  with and without a guider (`_maybe_recover_guiding`, `_setup_target`):
  "... skipping <name>".

Each was followed at once by the driver's "retried on the next pass": a
claim nothing keeps, the class of #290 and #302. Each now says, for a group
member, that the visit stops and the panel is retried on the next pass (set
aside tonight only after the streak), and keeps its sentence for a target in
no group (`_deferred_words`).

THE CLOUD HOLD IS STOOD IN FOR, as test_group_preslew_floor_wait.py stands in
for the pre-slew gate's hold: at the panel's second frame gate the mount's
floor is raised above it and the REAL `_hold_for_clear` is opened, with the
reason a cloud verdict would give; its first look finds the floor, and the
floor is put back once the hold has ended. A real hold needs a cloud verdict
from the frames, which the simulator cannot make (its frames are no sky).
The #72 bound and the guide start under skip are reached, for a member, only
in a group the pass rule sent on unguided under warn, and then only with
guiding required and skip: the rig's escalation is changed mid-run, at the
night's first exposure, as an operator's edit would change it.

Every case runs the real scheduler, hop, frame loop, hold and gates on the
clocked simulator (tests/_group_harness.py). The fixture site is 40 N 74 W,
not anybody's rig, and no altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad/s4-engc-mut), never in the
shared tree (#254), and run again on the finished S4 tree on 2026-09-27
(scratchpad/s4-engc-resume-mut), where each failed as recorded.
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.config import EscalationConfig, SafetyConfig
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import session_store

#: What a group member's gate now says happens next (`_deferred_words`).
RETRIED = ("the visit to {label} stops, and the panel is retried on the next "
           "pass (set aside tonight only after 3 consecutive failed visits)")
#: What the #72 bound says when it gives up (`_maybe_recover_guiding`).
GAVE_UP = ("guiding could not be kept after 2 recovery attempts without a "
           "frame")
DASH = "\u2014"
#: A floor no target of these nights reaches.
HIGH_FLOOR = 89.0


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _single_plan(*names: str, **plan_kw) -> SequencePlan:
    targets = [single(n, filters=("L", "R"), count=3, ha_h=-2.0 - 0.1 * i)
               for i, n in enumerate(names)]
    base = dict(name="singles", guide=False, dither_every=0,
                autofocus_every=0, meridian_flip=False, park_when_done=False,
                warm_cooler_when_done=False, recover_guiding=False)
    base.update(plan_kw)
    return SequencePlan(targets=targets, **base)


async def _run(night, plan) -> Night:
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _raise_the_mount_floor(store):
    """The mount's floor above every target while the hold looks, and back
    to none once it has ended: ``(before, after)`` for the stand-in."""
    def before(target):
        store.set_safety(SafetyConfig(enabled=False, min_alt_deg=HIGH_FLOOR))

    def after(target):
        store.set_safety(SafetyConfig(enabled=False))
    return before, after


def _stand_in_a_cloud_hold(night, monkeypatch, who: str, floor,
                           nth: int = 2) -> list[float]:
    """At ``who``'s ``nth`` frame gate, run ``floor[0](target)`` to put a
    floor above it and open the real `_hold_for_clear` for it, as the frame
    loop's sky verdict opens one; the hold ends at its first look, on the
    floor, and ``floor[1](target)`` then runs. Returns when the hold
    opened."""
    eng = night.engine
    spy = eng._safety_gate
    held: list[float] = []
    seen = [0]
    before, after = floor

    async def gate(*a, **kw):
        target = kw.get("target")
        if (kw.get("context") == "frame" and target is not None
                and target.name == who and not held):
            seen[0] += 1
            if seen[0] == nth:
                held.append(night.rel(engine_mod.time.time()))
                before(target)
                try:
                    await eng._hold_for_clear(
                        "no safety monitor is assigned and the frames say the "
                        "sky has closed in", target)
                finally:
                    after(target)
        return await spy(*a, **kw)

    monkeypatch.setattr(eng, "_safety_gate", gate)
    return held


def _details(night) -> list[str]:
    return [s.get("detail") for s in night.states if s.get("detail")]


# ------------------------------------------------- the cloud hold's floor

async def test_a_panels_cloud_hold_floor_stop_says_the_panel_is_retried(
        group_hub, group_store, monkeypatch):
    """A 1x2 of L and R, 3 frames each, one frame a visit. A cloud hold opens
    at 1-2's second frame gate and its first look finds the mount's floor.
    The hold's line says tracking stops and 1-2's visit stops, with the
    panel retried on the next pass; the hold's published end says the same;
    neither says 1-2 was set aside. 1-2 IS retried: it is deferred (1 of 3),
    shot on the next pass, and the run completes with nothing set aside.

    MUTANT "single-target sentence for a panel" (`_deferred_words`
    returning None): the hold says the panel is set aside for the rest of
    the run, and the driver's next line retries it. RED (observed; the
    mutant "the hold floor line alone back", ``then = None`` in
    `_hold_watch` only, fails it with the same lines):
        AssertionError: ["M31 1-2 reaches the mount's altitude floor during
        the cloud hold - stopping tracking and setting it aside for the rest
        of this run"]
        assert ['M31 1-2 rea... of this run'] == ['M31 1-2 rea...iled
        visits)']
          At index 0 diff: "M31 1-2 reaches the mount's altitude floor
          during the cloud hold - stopping tracking and setting it aside for
          the rest of this run" != "M31 1-2 reaches the mount's altitude
          floor during the cloud hold - stopping tracking; the visit to 1-2
          stops, and the panel is retried on the next pass (set aside tonight
          only after 3 consecutive failed visits)"
    MUTANT "the hold's end alone back" (``then = None`` in
    `_hold_for_clear`'s StopTarget arm only). RED (observed):
        AssertionError: ['cloud hold ended: M31 1-2 was set aside']
        assert ['cloud hold ...as set aside'] == ['cloud hold ...iled
        visits)']
    """
    night = Night(group_hub, monkeypatch)
    held = _stand_in_a_cloud_hold(night, monkeypatch, _name("1-2"),
                                  _raise_the_mount_floor(group_store))
    await _run(night, grid_plan(rows=1, cols=2))
    assert night.done, night.trace[-3:]
    assert len(held) == 1, f"premise: the hold opened once: {held}"
    floor = night.said("reaches the mount's altitude floor during the cloud "
                       "hold")
    assert floor == [f"{_name('1-2')} reaches the mount's altitude floor "
                     f"during the cloud hold - stopping tracking; "
                     + RETRIED.format(label="1-2")], floor
    ended = [d for d in _details(night) if d.startswith("cloud hold ended")]
    assert ended == ["cloud hold ended: " + RETRIED.format(label="1-2")], ended
    assert not [m for _t, _l, m in night.lines if "set aside" in m
                and "1-2" in m and "only after" not in m], night.lines
    assert night.said(f"{GROUP_NAME}: the visit stopped on 1-2: "
                      f"{_name('1-2')} reached the mount's altitude floor "
                      f"during a cloud hold; retried on the next pass (1 of "
                      f"3 consecutive)"), night.said("1-2")
    assert night.engine.state.get("end_reason") == "complete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.stored.set_aside == []


async def test_control_a_single_targets_cloud_hold_floor_stop_keeps_its_words(
        group_hub, group_store, monkeypatch):
    """Control: the same hold on a target in no group. It IS set aside for
    the rest of the run (the scheduler drops it), and the hold's line and
    published end say so, as they always have.

    MUTANT "every target reads as a panel" (`_deferred_words` answering the
    member's words for a target in no group too, "the visit to Solo
    stops..."). RED (observed):
        AssertionError: ["Solo reaches the mount's altitude floor during the
        cloud hold - stopping tracking; the visit to Solo stops, and the
        panel is retried on the next pass (set aside tonight only after 3
        consecutive failed visits)"]
        assert ['Solo reache...iled visits)'] == ['Solo reache... of this
        run']
    """
    night = Night(group_hub, monkeypatch)
    held = _stand_in_a_cloud_hold(night, monkeypatch, "Solo",
                                  _raise_the_mount_floor(group_store))
    await _run(night, _single_plan("Solo"))
    assert night.done, night.trace[-3:]
    assert len(held) == 1, f"premise: the hold opened once: {held}"
    floor = night.said("reaches the mount's altitude floor during the cloud "
                       "hold")
    assert floor == ["Solo reaches the mount's altitude floor during the "
                     "cloud hold - stopping tracking and setting it aside for "
                     "the rest of this run"], floor
    ended = [d for d in _details(night) if d.startswith("cloud hold ended")]
    assert ended == ["cloud hold ended: Solo was set aside"], ended
    assert [who for who, _f in night.shots()].count("Solo") == 1


async def test_control_a_panels_own_floor_in_the_hold_still_says_set_aside(
        group_hub, group_store, monkeypatch):
    """Control: the look finds 1-2's OWN floor instead (``on_floor``
    advance), raised above it as the hold opens. That is a ``FloorStop``,
    which the driver does set aside tonight (`_visit_panel`), so the hold's
    published end says 1-2 was set aside, as it always has, and 1-2 is.

    MUTANT "every StopTarget reads as a deferral" (the hold's end asks
    `_deferred_words` without the ``_STOPS_NOT_DEFERRED`` test). RED
    (observed):
        AssertionError: ['cloud hold ended: the visit to 1-2 stops, and the
        panel is retried on the next pass (set aside tonight only after 3
        consecutive failed visits)']
        assert ['cloud hold ...iled visits)'] == ['cloud hold ...as set
        aside']
    """
    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"schedule_kw": {"min_altitude_deg": 10.0,
                                               "on_floor": "advance"}})

    def before(target):
        target.schedule.min_altitude_deg = HIGH_FLOOR

    night = Night(group_hub, monkeypatch)
    held = _stand_in_a_cloud_hold(night, monkeypatch, _name("1-2"),
                                  (before, lambda target: None))
    await _run(night, plan)
    assert night.done, night.trace[-3:]
    assert len(held) == 1, f"premise: the hold opened once: {held}"
    ended = [d for d in _details(night) if d.startswith("cloud hold ended")]
    assert ended == [f"cloud hold ended: {_name('1-2')} was set aside"], ended
    assert [r["target_id"] for r in night.stored.set_aside] == ["p01"], (
        night.stored.set_aside)


# ---------------------------------------- the guide start and the #72 bound

def _unguided_then_required(night, store) -> None:
    """At the night's first exposure (the first pass shoots none, every
    guide start failing), change the rig's escalation to guiding required
    and skip, as an operator's edit mid-run would."""
    switched: list[str] = []

    def on_capture(rec):
        if not switched:
            switched.append(rec["target"])
            store.set_escalation(EscalationConfig(require_guiding=True,
                                                  guiding_action="skip"))

    night.on_capture = on_capture


async def test_a_panels_guide_skips_in_a_group_sent_on_unguided_say_it_is_retried(
        group_hub, group_store, monkeypatch):
    """A 1x2 of one filter, four frames a visit, whose guider never starts,
    guiding optional under warn. Pass 1: both starts fail, the pass rule
    blames the rig, and warn sends the panels on unguided. Pass 2: 1-1's
    start fails, it is shot on unguided, the escalation becomes guiding
    required and skip at its first exposure, and its two recovery attempts
    fail too, so the #72 bound gives up under skip; then 1-2's start fails
    under skip. Both lines say the visit stops and the panel is retried on
    the next pass, and both panels are deferred, never dropped.

    MUTANT "single-target sentence for a panel" (`_deferred_words`
    returning None): both say "skipping M31 1-1" and "skipping M31 1-2".
    RED (observed; "the bound's skip line alone back", ``then = None`` in
    `_maybe_recover_guiding` only, fails it with the same lines):
        AssertionError: ['guiding could not be kept after 2 recovery
        attempts without a frame \u2014 skipping M31 1-1']
        assert ['guiding cou...ping M31 1-1'] == ['guiding cou...iled
        visits)']
    MUTANT "the failed start's skip line alone back" (``then = None`` in
    `_setup_target`'s failed-start branch only). RED (observed):
        AssertionError: ['guiding required but failed to start: no guide
        star found (attempt 2) \u2014 skipping M31 1-2', 'guiding required
        but fai...mpt 6) \u2014 skipping M31 1-1', 'guiding required but
        failed to start: no guide star found (attempt 4) \u2014 skipping M31
        1-2']
        assert ['guiding req...ping M31 1-2'] == ['guiding req...iled
        visits)']
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    plan = grid_plan(rows=1, cols=2, guide=True, recover_guiding=True,
                     count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=20,
                     max_consecutive_rejects_night=0,
                     panel_kw={"filters": ("L",), "count": 12,
                               "per_visit": 4})
    guider = group_hub.guider
    night = Night(group_hub, monkeypatch, guide=lambda who, n: False,
                  stars=lambda who, f: 50 if guider.active else 0)
    _unguided_then_required(night, group_store)
    await _run(night, plan)
    assert night.done, night.trace[-3:]
    assert night.said("continuing the panels unguided"), (
        "premise: pass 1 sent the group on unguided", night.lines[:12])
    bound = night.said(GAVE_UP + f" {DASH} ")
    assert bound[:1] == [f"{GAVE_UP} {DASH} "
                         + RETRIED.format(label="1-1")], bound
    start = night.said("guiding required but failed to start: ")
    assert start[:1] == [f"guiding required but failed to start: no guide "
                         f"star found (attempt 2) {DASH} "
                         + RETRIED.format(label="1-2")], start
    assert not night.said(f"skipping {GROUP_NAME}"), night.said("skipping")
    assert night.said(f"{GROUP_NAME}: the visit stopped on 1-1: {GAVE_UP}; "
                      f"retried on the next pass (1 of 3 consecutive)"), (
        night.said("the visit stopped"))


async def test_a_panels_no_guider_skip_in_a_group_sent_on_unguided_says_it_is_retried(
        group_hub, group_store, monkeypatch):
    """The same with no guider connected at all. Pass 1: both panels defer,
    the pass rule blames the rig, and warn sends them on unguided. Pass 2:
    1-1 is shot unguided, the escalation becomes guiding required and skip
    at its first exposure, and 1-2's hop finds no guider under skip: its
    line says the visit stops and the panel is retried on the next pass.

    MUTANT "single-target sentence for a panel" (`_deferred_words`
    returning None). RED (observed; "the no-guider skip line alone back",
    ``then = None`` in `_setup_target`'s no-guider branch only, fails it
    with the same lines):
        AssertionError: ['guiding required but the guider (scripted guider)
        is not connected \u2014 skipping M31 1-2', 'guiding required but
        the gu...connected \u2014 skipping M31 1-1', 'guiding required but
        the guider (scripted guider) is not connected \u2014 skipping M31
        1-1']
        assert ['guiding req...ping M31 1-2'] == ['guiding req...iled
        visits)']
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    group_hub.guider.connected = False
    night = Night(group_hub, monkeypatch)
    _unguided_then_required(night, group_store)
    await _run(night, grid_plan(rows=1, cols=2, guide=True))
    assert night.done, night.trace[-3:]
    assert night.said("continuing the panels unguided"), (
        "premise: pass 1 sent the group on unguided", night.lines[:12])
    words = night.said("guiding required but the guider (scripted guider) is "
                       "not connected ")
    assert words[:1] == [f"guiding required but the guider (scripted guider) "
                         f"is not connected {DASH} "
                         + RETRIED.format(label="1-2")], words
    assert not night.said(f"skipping {GROUP_NAME}"), night.said("skipping")


@pytest.mark.parametrize("guider", ["fails", "absent"])
async def test_control_a_single_targets_guide_skips_keep_their_words(
        group_hub, group_store, monkeypatch, guider):
    """Control: the same two nights with targets in no group, Alpha then
    Bravo. Each gate that stops one says it is skipped, as it always has:
    the #72 bound on Alpha (when the guider is there and never starts) and
    Bravo's guide start under skip, with and without a guider.

    MUTANT "every target reads as a panel" (`_deferred_words` answering the
    member's words for a target in no group too). RED on each (observed):
        fails:
            AssertionError: ['guiding could not be kept after 2 recovery
            attempts without a frame \u2014 the visit to Alpha stops, and
            the panel is retried on the next pass (set aside tonight only
            after 3 consecutive failed visits)']
        absent:
            AssertionError: ['guiding required but the guider (scripted
            guider) is not connected \u2014 the visit to Bravo stops, and
            the panel is retried on the next pass (set aside tonight only
            after 3 consecutive failed visits)']
    (each followed by ``assert ['guiding ...iled visits)'] == ['guiding
    ...ipping <name>']``).
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    if guider == "absent":
        group_hub.guider.connected = False
        night = Night(group_hub, monkeypatch)
        plan = _single_plan("Alpha", "Bravo", guide=True)
    else:
        hub_guider = group_hub.guider
        night = Night(group_hub, monkeypatch, guide=lambda who, n: False,
                      stars=lambda who, f: 50 if hub_guider.active else 0)
        plan = _single_plan("Alpha", "Bravo", guide=True,
                            recover_guiding=True, count_mode="accepted",
                            min_stars=5, max_consecutive_rejects=20,
                            max_consecutive_rejects_night=0)
    _unguided_then_required(night, group_store)
    await _run(night, plan)
    assert night.done, night.trace[-3:]
    if guider == "fails":
        bound = night.said(GAVE_UP + f" {DASH} ")
        assert bound == [f"{GAVE_UP} {DASH} skipping Alpha"], bound
        start = night.said("guiding required but failed to start: ")
        assert start == [f"guiding required but failed to start: no guide "
                         f"star found (attempt 1) {DASH} skipping Bravo"], (
            start)
    else:
        words = night.said("guiding required but the guider (scripted "
                           "guider) is not connected ")
        assert words == [f"guiding required but the guider (scripted guider) "
                         f"is not connected {DASH} skipping Bravo"], words
    assert not night.said("retried on the next pass"), night.lines[-6:]


# ------------------------------------------- a refused hop is not a visit

async def test_a_hop_refused_before_any_slew_is_not_the_grids_last_visit(
        group_hub, group_store, monkeypatch):
    """A 1x3 in ``grid`` order, one filter, two frames each, one a visit.
    1-3's first pre-slew gate finds the mount's floor raised above it and
    refuses the hop before any slew; the floor is put back at once. 1-3
    waits (a reach wait, no visit), and the pass ends with 1-1 and 1-2
    visited. The grid goes on from the panel the mount last VISITED, 1-2,
    so the next pass starts at 1-3: 1-1, 1-2, 1-3, 1-1, 1-2, 1-3.

    MUTANT "refused hop recorded as visited" (`_visit_panel`'s ``finally``
    recording ``_last_visit_ts`` and ``_group_last_visited`` for every hop,
    the refused one included): the grid goes on from 1-3, where the mount
    never went, and 1-3 waits a whole pass more. RED (observed):
        AssertionError: ['1-1', '1-2', '1-1', '1-2', '1-3', '1-3']
        assert ['1-1', '1-2'... '1-3', '1-3'] == ['1-1', '1-2'... '1-2',
        '1-3']
          At index 2 diff: '1-1' != '1-3'
    """
    night = Night(group_hub, monkeypatch)
    eng = night.engine
    spy = eng._safety_gate
    refused: list[float] = []

    async def gate(*a, **kw):
        target = kw.get("target")
        if (kw.get("context") == "slew" and target is not None
                and target.name == _name("1-3") and not refused):
            refused.append(night.rel(engine_mod.time.time()))
            group_store.set_safety(SafetyConfig(enabled=False,
                                                min_alt_deg=HIGH_FLOOR))
            try:
                return await spy(*a, **kw)
            finally:
                group_store.set_safety(SafetyConfig(enabled=False))
        return await spy(*a, **kw)

    monkeypatch.setattr(eng, "_safety_gate", gate)
    plan = grid_plan(rows=1, cols=3, group_kw={"order": "grid"},
                     panel_kw={"filters": ("L",), "count": 2})
    await _run(night, plan)
    assert night.done, night.trace[-3:]
    assert len(refused) == 1, f"premise: 1-3's hop was refused once: {refused}"
    assert night.said(f"{GROUP_NAME}: 1-3 cannot be slewed to now: "), (
        "premise: the refusal was a reach wait", night.lines[:12])
    order = [who[len(GROUP_NAME) + 1:] for _t, who in night.gotos]
    assert order == ["1-1", "1-2", "1-3", "1-1", "1-2", "1-3"], order
    assert night.engine.state.get("end_reason") == "complete"


async def test_a_hop_refused_before_any_slew_is_not_the_least_completes_visit(
        group_hub, group_store, monkeypatch):
    """The same for ``least_complete``, the default order, which reads each
    member's last visit time (`_order_snapshot`, ``_last_visit_ts``) where
    ``grid`` reads the panel visited last. A 1x2, one filter, two frames
    each, one a visit, guiding asked. Pass 1: 1-1's first guider start
    fails, so 1-1 is visited and deferred, shooting nothing; 1-2's first
    pre-slew gate finds the mount's floor raised above it and refuses the
    hop before any slew, and the floor is put back at once. The pass ends
    with both panels at nothing banked, 1-1 visited and 1-2 never visited,
    so after the deferral wait the next pass leads with 1-2 (a panel never
    visited leads, then the oldest): 1-1, 1-2, 1-1, 1-2, 1-1.

    MUTANT "the refused hop's visit time recorded" (the verifier's, run in
    scratchpad/s4-engc-verify-mut: `_visit_panel`'s ``finally`` recording
    ``_last_visit_ts`` for every hop, the refused one included, with
    ``_group_last_visited`` still skipped for it, so the grid case above
    stays green, observed): 1-2 reads as the panel visited last, and the
    next pass leads with 1-1 again. RED (observed):
        AssertionError: ['1-1', '1-1', '1-2', '1-1', '1-2']
        assert ['1-1', '1-1'... '1-1', '1-2'] == ['1-1', '1-2'... '1-2',
        '1-1']
          At index 1 diff: '1-1' != '1-2'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=False,
                                                guiding_action="warn"))
    night = Night(group_hub, monkeypatch,
                  guide=lambda who, n: not (who == _name("1-1") and n == 1))
    eng = night.engine
    spy = eng._safety_gate
    refused: list[float] = []

    async def gate(*a, **kw):
        target = kw.get("target")
        if (kw.get("context") == "slew" and target is not None
                and target.name == _name("1-2") and not refused):
            refused.append(night.rel(engine_mod.time.time()))
            group_store.set_safety(SafetyConfig(enabled=False,
                                                min_alt_deg=HIGH_FLOOR))
            try:
                return await spy(*a, **kw)
            finally:
                group_store.set_safety(SafetyConfig(enabled=False))
        return await spy(*a, **kw)

    monkeypatch.setattr(eng, "_safety_gate", gate)
    plan = grid_plan(rows=1, cols=2, guide=True,
                     panel_kw={"filters": ("L",), "count": 2})
    assert plan.groups[0].order == "least_complete", "premise: the default"
    await _run(night, plan)
    assert night.done, night.trace[-3:]
    assert len(refused) == 1, f"premise: 1-2's hop was refused once: {refused}"
    assert night.said(f"{GROUP_NAME}: 1-2 cannot be slewed to now: "), (
        "premise: the refusal was a reach wait", night.lines[:12])
    assert night.visits()[0] == (_name("1-1"), ()), (
        "premise: 1-1's first visit was deferred", night.visits())
    order = [who[len(GROUP_NAME) + 1:] for _t, who in night.gotos]
    assert order == ["1-1", "1-2", "1-1", "1-2", "1-1"], order
    assert night.engine.state.get("end_reason") == "complete"
