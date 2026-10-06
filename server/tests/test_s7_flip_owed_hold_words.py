# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flip-owed hold's re-slews say what the hold is doing, in its own
words, and the flip gate's first no-op sentence is said once, for the
lead-time attempt it was written for (#456; spec 5.7, S7-ENG-FLIP).

THE DEFECT. `_hold_for_owed_flip` makes the flip itself: each 30 s poll
clears the target from `_flip_no_op`, re-arms the latch and calls the flip
gate. So every re-slew that flipped nothing took the gate's FIRST no-op
branch, and logged at warning the sentence written for the lead-time
attempt: "nothing flipped. Holding the flip armed and the retry waits
until 15 s past the meridian itself". Inside the hold that promise is not
what happens: the hold makes the next attempt itself, 30 s later. On a
mount no re-slew flips, the crossing night logged 41 of those warnings, 1
for the lead-time attempt and 40 from the hold's 20 minutes, each one
reaching a default alert sink that takes warnings.

THE FIX. The hold marks the attempt it is making (``_flip_owed_attempt``,
its count, set around each call to the gate), and a re-slew the hold made
that flipped nothing says so in the hold's words, at info, one line per
attempt: "the flip-owed hold's re-slew 3 changed nothing ... still
holding". The latch is left as the first no-op branch left it. The hold's
own error line at its start and its StopTarget at its end still say what
matters, at error and in the report.

THE NIGHT is #456's: the golden flow plan from the harness's ``T0`` on the
clocked simulator (tests/_group_harness.py), NGC 7331 crossing the fixture
site's meridian 180 min in, with the mount's ``pier_side`` patched to answer
west whatever its gotos do: a mount no re-slew ever flips.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). The mutant was applied in a
private scratch copy of server/ (scratchpad S7-ENG-FLIP-mut, from a byte
backup), never in the shared tree (#254):

* "hold attempts take the gate's first no-op branch": the gate reading
  ``owed_attempt`` as 0, so its hold-attempt branch is never taken and a
  re-slew the hold made falls into the lead-time attempt's branch, as it
  did before #456.
* "the hold's mark never cleared": the ``finally`` in `_hold_for_owed_flip`
  that sets ``_flip_owed_attempt`` back to 0 after each call to the gate
  removed, so the mark outlives the hold that made it.
* "the hold's branch spends the latch": the gate's hold branch without its
  ``_flip_no_op.add(key)`` and ``_flip_armed = True``, so the claim that
  only the words changed is kept by nothing.

The last two were added by the S7-ENG-FLIP verifier, in its own private
copies (scratchpads S7-ENG-FLIP-verify-mut and -verify-new): before the
case that now catches both, the whole server suite run under each failed
exactly the cases it failed unmutated, so nothing held either.
"""
from __future__ import annotations

import re

from _group_harness import (LON, T0, Night, close_night_hub,  # noqa: F401
                            group_hub, group_store, night_hub, single)
from astrodeck.devices.base import PierSide
from astrodeck.sequence import SequencePlan, schedule
from test_group_rotation import _golden_as_recorded

GOLDEN = "NGC 7331"
FIRST_NO_OP = "nothing flipped. Holding the flip armed"
HOLD_WORDS = "the meridian flip hold's re-slew"


def _past_s(t: float) -> float:
    (target,) = _golden_as_recorded().targets
    return -schedule.hours_to_meridian_flip(target.ra_hours, LON, t) * 3600.0


async def _never_flipping_night(monkeypatch) -> Night:
    hub, popped = await night_hub(monkeypatch)
    try:
        night = Night(hub, monkeypatch, t0=T0)

        async def west():
            return PierSide.WEST

        monkeypatch.setattr(hub.devices["telescope"], "pier_side", west)
        try:
            night.done = await night.run(_golden_as_recorded(), wall_s=120.0)
        finally:
            await night.close()
    finally:
        await close_night_hub(hub, popped)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    return night


async def test_the_holds_re_slews_say_so_in_the_holds_words(group_store,
                                                           monkeypatch):
    """The gate's first no-op sentence, "Holding the flip armed ...", is
    said ONCE, at warning, for the lead-time attempt ten minutes before
    transit. The retry past the meridian says "nothing flipped this time
    either" once. Then the flip-owed hold holds, and every re-slew it makes
    that flips nothing says so in the hold's words, one line per attempt,
    numbered from 1, at info, and none of them promises a retry: as many
    lines as the hold made re-slews, 40 on this night. The hold ends as it
    always has, with the StopTarget that moves the night on.

    MUTANT "hold attempts take the gate's first no-op branch" (the gate's
    ``owed_attempt`` read as 0, so its hold-attempt branch is never
    taken): RED (observed), the lead-time sentence once for each of the
    hold's 40 re-slews as well, #456's 41 warnings:
        AssertionError: the lead-time attempt's sentence was said 41 times,
        the first at [10172.28, 10785.603, 10815.603] s
        assert 41 == 1
    test_flip_retry_margin.py stays green under it (observed): it counts
    the lead-time sentence only on the night whose retry flips, where no
    hold runs.
    """
    night = await _never_flipping_night(monkeypatch)
    first = [(round(night.rel(t), 3), lvl) for t, lvl, m in night.lines
             if FIRST_NO_OP in m]
    assert len(first) == 1, (
        f"the lead-time attempt's sentence was said {len(first)} times, "
        f"the first at {[t for t, _l in first[:3]]} s")
    t_first = next(t for t, _l, m in night.lines if FIRST_NO_OP in m)
    assert first[0][1] == "warning", first
    assert 590.0 <= -_past_s(t_first) <= 610.0, (
        f"the sentence was not the lead-time attempt's: "
        f"{-_past_s(t_first):.1f} s before transit")
    assert len(night.said("nothing flipped this time either")) == 1

    # The hold's re-slews, in the trace's order: the retry's goto and the
    # hold's first share one instant, so the clock cannot tell them apart.
    logs = [i for i, e in enumerate(night.trace) if e[1] == "log"]
    opened = [i for i in logs
              if "a meridian flip is required and the mount is still on the west "
                 "side -- refusing to expose" in night.trace[i][3]]
    ended = [i for i in logs
             if "a meridian flip has been pending for 20 min" in night.trace[i][3]]
    assert len(opened) == 1 and len(ended) == 1, (opened, ended)
    hold_gotos = [e[0] for e in night.trace[opened[0]:ended[0]]
                  if e[1] == "goto" and e[2] == GOLDEN]
    assert len(hold_gotos) >= 2, f"premise: the hold re-slewed: {hold_gotos}"
    words = [(lvl, m) for t, lvl, m in night.lines if HOLD_WORDS in m]
    assert len(words) == len(hold_gotos) == 40, (
        f"{len(words)} lines in the hold's words for {len(hold_gotos)} "
        f"re-slews: {words[:2]}")
    assert {lvl for lvl, _m in words} == {"info"}, words[:2]
    assert words[0][1] == (
        f"{GOLDEN}: the meridian flip hold's re-slew 1 changed nothing — the "
        f"mount still reports pier side west; still holding, and nothing is "
        f"exposed while it stays on that side"), words[0][1]
    assert words[-1][1].startswith(
        f"{GOLDEN}: the meridian flip hold's re-slew 40 changed nothing"), (
        words[-1][1])
    assert not [m for _l, m in words if "retry" in m], words[:2]
    assert not night.said("meridian flip complete"), (
        night.said("meridian flip complete"))


async def test_the_holds_mark_is_gone_when_the_next_target_crosses(
        group_hub, monkeypatch):
    """The hold marks the attempt it is making only around its call to the
    gate (``_flip_owed_attempt``, cleared in a ``finally``), so once a hold
    has ended the next target's own attempts are said in the gate's words
    again, and leave the latch as the gate's own branches leave it. Two
    targets on a mount no re-slew flips (``pier_side`` always west), shot
    one after the other: A crosses 15 min in, holds its 20 min and is moved
    on; B crosses 45 min in, makes its own zero-lead attempt (the mount has
    shown it cannot flip early, #127) and its retry, and holds in turn.
    Each target says "Holding the flip armed" once and "nothing flipped
    this time either" once, and each hold numbers its own re-slews from 1
    to 40.

    Left set, the mark changes more than a sentence: the hold's branch
    re-arms the latch, and the retry's branch spends it, so a mark that
    outlived its hold sent B's retry down the hold's branch and left the
    latch armed behind it.

    MUTANT "the hold's mark never cleared" (the ``finally`` that sets
    ``_flip_owed_attempt`` back to 0 removed): RED (observed), B's own
    attempt and its retry both said as re-slew 40 of A's ended hold, with
    no gate sentence for B at all:
        AssertionError: {'A': (1, 1, [1, 2, 3], 40), 'B': (0, 0, [40, 40,
        1], 42)}
        Differing items:
        {'B': (0, 0, [40, 40, 1, 2, 3, 4, ...])} != {'B': (1, 1, [1, 2, 3,
        4, 5, 6, ...])}
    The case above stays green under it (observed): its night has one
    target, and nothing is attempted after its hold.
    MUTANT "the hold's branch spends the latch" (``self._flip_no_op.add(
    key)`` and ``self._flip_armed = True`` removed from the gate's hold
    branch): RED (observed), on the last assertion:
        AssertionError: assert ([], False) == (['a', 'b'], True)
    """
    plan = SequencePlan(
        name="two holds", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=True, park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False,
        targets=[single("A", count=40, ha_h=-0.25),
                 single("B", count=60, ha_h=-0.75)])
    night = Night(group_hub, monkeypatch, t0=T0)

    async def west():
        return PierSide.WEST

    monkeypatch.setattr(group_hub.devices["telescope"], "pier_side", west)
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    for name in ("A", "B"):
        ended = f"{name}: a meridian flip has been pending for 20 min"
        assert night.said(ended), (
            f"premise: {name}'s hold ran to its end: "
            f"{night.said(name + ': ')[-3:]}")

    def said(name: str) -> tuple[int, int, list[int]]:
        mine = [m for _t, _l, m in night.lines if m.startswith(f"{name}: ")]
        return (sum(FIRST_NO_OP in m for m in mine),
                sum("nothing flipped this time either" in m for m in mine),
                [int(n) for m in mine
                 for n in re.findall(HOLD_WORDS + r" (\d+) changed", m)])

    got = {name: said(name) for name in ("A", "B")}
    want = (1, 1, list(range(1, 41)))
    assert got == {"A": want, "B": want}, {
        name: (first, retry, numbers[:3], len(numbers))
        for name, (first, retry, numbers) in got.items()}
    # ONLY THE WORDS CHANGED (the gate's own comment on its hold branch):
    # each hold's last re-slew left the latch as the lead-time branch
    # leaves it, armed, with the target in `_flip_no_op`, so a target taken
    # up again is owed the zero-lead band and its retry's words.
    engine = night.engine
    assert (sorted(engine._flip_no_op), engine._flip_armed) == (
        sorted(t.id for t in plan.targets), True)
