# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 6.2 (#180 part B, owner ruling D-E, 2026-10-22) on the clocked
simulator: a mosaic panel that fails is skipped for the next panel and
revisited every round. It is never dropped.

The fixture is the group harness's 2x2 (tests/_group_harness.py), each panel
owing eight cycles of L and R, one cycle a visit, so the group runs eight
rounds. The sky is scripted: panel 2-2 does not centre. Snake order puts the
panels 1-1, 1-2, 2-2, 2-1.

THESE CASES ARE A DELIBERATE BEHAVIOUR CHANGE. Until D-E a panel that failed
three visits in a row was set aside for the night, or, for a centring streak,
parked once for 45 minutes. Now it is skipped at its third failure: it stays
in the rotation behind the panels that have not failed, and the only thing
that can end the night for it is a GROUP rule (D-03's held passes, once it is
the last live panel).

Each case names its mutant. The named mutant of the work package, "drop the
panel after three failures", is the first.
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.config import EscalationConfig
from astrodeck.events import night_key
from astrodeck.sequence.session import session_store

ROUNDS = 8
LABELS = ["1-1", "1-2", "2-2", "2-1"]


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _plan():
    return grid_plan(panel_kw={"count": ROUNDS})


def _never_centres(label: str):
    def goto(who, n, result):
        if who == _name(label):
            return {**result, "centered": False, "error_arcmin": None}
        return result
    return goto


async def _run(hub, monkeypatch, plan, *, session=None, t0=T0, **kw) -> Night:
    night = Night(hub, monkeypatch, t0=t0, **kw)
    start_kw = {} if session is None else {"session": session}
    try:
        night.done = await night.run(plan, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _hops(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


def _labels(night) -> list[str]:
    return [who[len(GROUP_NAME) + 1:] for _t, who in night.gotos]


async def test_a_panel_that_fails_every_visit_is_visited_every_round_last(
        group_hub, monkeypatch):
    """2-2 never centres while its neighbours shoot. It is hopped in every one
    of the eight rounds, at the back of the round from the fourth on (it is
    skipped at its third failure, at the close of round three), never set
    aside for its own failures, and recorded as skipped. Its neighbours
    complete. Once they have, 2-2 is the mosaic's last live panel, its misses
    are held (D-03: the sky, not the panel) and the group's rule, six held
    passes, is the only thing that sets it aside, kind "deferred".

    MUTANT "drop the panel after three failures" (`GroupRun._count_failure`
    and the reject rule restored to the set-aside at ``max_failed_visits``:
    the ``if self.rotate:`` arm made ``if False:``). RED, observed: 2-2 is
    set aside "for now" at its third failure and visited again once at the
    45-minute expiry, not every round. Observed:
        AssertionError: [['1-1', '1-2', '2-2', '2-1'], ['2-2', '1-1', '1-2',
        '2-1'], ['2-2', '1-1', '1-2', '2-1'], ['1-1', '1-2', '2-1', '1-1'],
        ...]
        assert False  (where False = all(<each round holds the four panels
        once>))
    """
    night = await _run(group_hub, monkeypatch, _plan(),
                       goto=_never_centres("2-2"))
    assert night.done, (len(night.gotos), night.lines[-3:])
    labels = _labels(night)
    rounds = [labels[i:i + 4] for i in range(0, 4 * ROUNDS, 4)]
    assert all(sorted(r) == sorted(LABELS) for r in rounds), rounds
    assert sum("2-2" in r for r in rounds) == ROUNDS, (
        f"2-2 was hopped in {sum('2-2' in r for r in rounds)} of {ROUNDS} "
        f"rounds")
    assert rounds[0] == ["1-1", "1-2", "2-2", "2-1"], rounds[0]
    assert all(r[-1] == "2-2" for r in rounds[3:]), (
        f"2-2 is last in the round from the fourth: {rounds}")
    for label in ("1-1", "1-2", "2-1"):
        assert [f for t, f in night.shots() if t == _name(label)] == [
            "L", "R"] * ROUNDS, label
    assert [f for t, f in night.shots() if t == _name("2-2")] == []
    stored = night.stored
    assert [(r["target_id"], r["kind"]) for r in stored.set_aside] == [
        ("p11", "deferred")], stored.set_aside
    assert "held for 6 passes" in stored.set_aside[0]["reason"]
    skips = stored.panel_skips
    assert [(r["target_id"], r["kind"], r["night"], r["visits"])
            for r in skips] == [("p11", "centring", night_key(T0), ROUNDS)], (
        skips)


async def test_the_first_skip_is_one_warning_and_one_report_line(
        group_hub, monkeypatch):
    """The skip is said at "warning" ONCE (an alert) and as an info line each
    later round, and the report carries one line, worded as retried every
    round, not skipped for the night. No line claims the panel is set aside
    for its own failures.

    MUTANT "warning every round" (the ``_skip_said`` guard made ``if
    False``). Observed:
        AssertionError: ['M31: centring failed on 2-2 on 3 consecutive visits:
        ...skipped for the rest of this round ...]
        assert 6 == 1
    """
    night = await _run(group_hub, monkeypatch, _plan(),
                       goto=_never_centres("2-2"))
    warned = [m for _t, lvl, m in night.lines
              if lvl == "warning" and "skipped for the rest of this round" in m]
    assert len(warned) == 1, warned
    assert "is retried every round" in warned[0]
    assert "never set aside for its own failures" in warned[0]
    info = [m for _t, lvl, m in night.lines
            if lvl == "info" and "skipped for the rest of this round" in m]
    assert len(info) == ROUNDS - 3, info
    assert not [m for _t, _l, m in night.lines
                if "2-2" in m and "set aside for now" in m]


async def test_a_panel_that_fails_then_succeeds_banks_and_leaves_the_rotation(
        group_hub, monkeypatch):
    """2-2 fails its first four visits (skipped at the third) and centres from
    the fifth. It banks all its frames, is complete, and its standing skip is
    gone (`Session.standing_skip`: a frame is newer than it); the record stays
    as history of the night.

    MUTANT "a skip never ends" (``standing_skip`` ignores frames). Observed:
        AssertionError: assert {'kind': 'centring', 'night': '2026-09-01',
        'target_id': 'p11', 'ts': 1788314409.0, ...} is None
    """
    def goto(who, n, result):
        if who == _name("2-2") and n <= 4:
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = await _run(group_hub, monkeypatch, _plan(), goto=goto)
    assert night.done
    assert [f for t, f in night.shots() if t == _name("2-2")] == ["L", "R"] * 8
    stored = night.stored
    assert stored.owed() == 0 and stored.set_aside == []
    assert [r["visits"] for r in stored.panel_skips] == [4]
    assert stored.standing_skip("p11") is None


async def test_a_restart_the_same_night_revisits_the_skipped_panel_last(
        group_hub, monkeypatch):
    """The first night is cut after four rounds (2-2 skipped at its third
    failure). A fresh engine resumes the session the same night: the skip is
    read back, so the first round of the restart puts 2-2 LAST, not third in
    the snake, and the panel is visited, not dropped.

    MUTANT "not restored" (the `_restore_panel_skips` call in
    `_build_groups` replaced by ``pass``). RED, observed:
        AssertionError: ['2-2', '1-1', '1-2', '2-1']
        assert ['2-2', '1-1', '1-2', '2-1'] == ['1-1', '1-2', '2-1', '2-2']
        (without the restore, 2-2, the least complete panel, leads the first
        round of the restart)
    """
    first = await _run(group_hub, monkeypatch, _plan(),
                       goto=_never_centres("2-2"), horizon_s=1100.0)
    assert first.done is False, "premise: the first night was cut short"
    assert first.stored.standing_skip("p11") is not None, "premise"
    assert first.stored.set_aside == [], first.stored.set_aside
    second = await _run(group_hub, monkeypatch, first.stored.plan,
                        session=first.stored, t0=first.clock.t,
                        goto=_never_centres("2-2"))
    assert night_key(second.t0) == night_key(T0), "premise: the same night"
    labels = _labels(second)
    assert labels[:4] == ["1-1", "1-2", "2-1", "2-2"], labels[:4]


# ------------------------------------------------ the rig verdict, via rotation

def _guide_plan():
    return grid_plan(guide=True)


async def _guide_night(hub, monkeypatch, bad):
    return await _run(hub, monkeypatch, _guide_plan(),
                      guide=lambda who, n: who not in bad)


async def test_two_panels_that_never_guide_rotate_and_never_abort_the_run(
        group_hub, group_store, monkeypatch):
    """1-2 and 2-1 never start guiding, 1-1 and 2-2 always do, under
    ``require_guiding`` with ``guiding_action`` abort. The two that guide
    complete. The two that do not are skipped at their third failure, and in
    the passes after that every panel ATTEMPTED is one already in the skip
    rotation, failing for its own reasons while others shot: that is not the
    guider's fault, so the verdict of 5.6 step 7 ("every attempted panel
    failed, the rig's fault") does not apply to it. Read as the rig's fault
    it would SafetyAbort the whole run over two panels' guide stars, which
    nothing did before D-E. The panels stay skipped, every round is a held
    pass (D-03), and the group's own rule sets the mosaic aside after six.

    MUTANT "drop the check" (``_decide_pass_end`` reads the guide-start
    verdict without the rotation test). RED, observed:
        AssertionError: ('unsafe', 'guiding required but it did not start on
        any panel of M31')
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="abort"))
    bad = {_name("1-2"), _name("2-1")}
    night = await _guide_night(group_hub, monkeypatch, bad)
    assert night.done, night.lines[-3:]
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended[0] == "incomplete", ended
    assert not night.said("did not start on any")
    for label in ("1-1", "2-2"):
        assert [f for t, f in night.shots() if t == _name(label)] == [
            "L", "R"] * 3, (ended, label)
    for label in ("1-2", "2-1"):
        assert [f for t, f in night.shots() if t == _name(label)] == []
        assert len(_hops(night, label)) == 9, (label, _hops(night, label))
    assert sorted((r["target_id"], r["kind"])
                  for r in night.stored.set_aside) == [
        ("p01", "group"), ("p10", "group")], night.stored.set_aside
    assert night.said("held for 3 passes in a row"), night.lines[-6:]


async def test_with_the_defaults_such_panels_are_never_shot_unguided(
        group_hub, monkeypatch):
    """The same night under the rig's defaults (guiding optional, action
    warn): the verdict would have sent the group on unguided and shot the
    two panels with no guide star. They are not shot at all.

    MUTANT "drop the check". RED, observed: the group goes unguided
    (``continuing the panels unguided``) and the failing panels are retried
    under that escalation.
    """
    bad = {_name("1-2"), _name("2-1")}
    night = await _guide_night(group_hub, monkeypatch, bad)
    assert night.done, night.lines[-3:]
    assert not night.said("continuing the panels unguided")
    assert not night.said("did not start on any")
    assert night.engine.state.get("end_reason") == "incomplete"
    for label in ("1-2", "2-1"):
        assert [f for t, f in night.shots() if t == _name(label)] == []
        assert len(_hops(night, label)) == 9, (label, _hops(night, label))
