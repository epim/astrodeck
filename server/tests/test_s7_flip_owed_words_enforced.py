# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the no-op retry's warning says about the next frame is what
`_enforce_flip_owed` then does with it, for each of `_flip_owed_words`' five
sentences (#482, S7 finding B13; #366, S5 orchestrator ruling 2, spec 5.7;
S7-ENG-FLIP).

THE GAP. When a target's one flip retry flips nothing, the flip gate warns
"nothing flipped this time either" and adds `_flip_owed_words`: what the
flip-owed invariant will do about the next frame. The sentence and the
invariant decide that with two separate chains of conditions, and nothing
tied them: test_flip_retry_margin.py pins each sentence for a state without
driving `_enforce_flip_owed`, and test_flip_owed_invariant.py drives the
invariant through states of its own without reading the sentence. Either
chain could drift with the suite green, and the night log would then tell
the operator the frame is held while it is exposed across the pier, or the
reverse.

THE CASES. Each is a real night on the clocked simulator
(tests/_group_harness.py): the golden flow plan, whose NGC 7331 crosses the
fixture site's meridian during the night, with the real flip gate, the real
retry `MERIDIAN_SIDE_MARGIN_S` past the crossing and the real frame loop,
staged so the retry's warning is worded from one of the five cases. The
case reads the sentence the retry logged, and then what the frame loop did
next, at the frame boundary the retry was made at, where
`_enforce_flip_owed` runs on the side the retry read: exposed the frame
("through"), or opened the flip-owed hold ("held"). The stagings, each a
double of the mount's pier-side read only (the gotos, the slews and the
simulator's own side are real):

* ``hold-off``: a mount no re-slew flips (``pier_side`` always west), and
  ``safety.flip_owed_hold_min`` 0.
* ``unreadable``: the engine's reads of the side answer "unknown" all night,
  while the hub's own check inside the flip (`Hub.pier_side_now`) reads west
  before and after each re-slew: the flicker `_maybe_meridian_flip`'s
  cross-check is written for, and the only way a no-op retry can be worded
  from an unreadable side (a side unreadable to both reads counts as
  flipped).
* ``no-record``: the night starts a minute past the crossing, so the target
  is never seen east of its meridian, on a mount no re-slew flips.
* ``other-side``: the side reads east at the target's first sighting and
  west from the fake 5000 s on, before the lead-time attempt.
* ``holds``: a mount no re-slew flips, the hold at its default 20 min.

WHAT THIS FOUND. The five sentences and the behaviour agree: no engine
change was needed for B13. The case-3 half of `_enforce_flip_owed`'s record
condition (``pre is None``) is subsumed by the other half on case 3's own
night (``side != pre`` is True for a readable side against no record), so
inverting that half literally leaves the no-record night as it was; the
named mutant for case 3 inverts what the half DOES (E3, below), and the
literal inversion is recorded beside it.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). The mutants were applied in a
private scratch copy of server/ (scratchpad S7-ENG-FLIP-mut, from a byte
backup), never in the shared tree (#254), each to `_enforce_flip_owed`, the
behaviour the sentence claims:

* E1 "hold off ignored": ``if hold_min <= 0:`` made ``if hold_min > 0:``.
* E2 "unreadable read as a side": ``if side not in ("east", "west"):`` made
  ``if side in ("east", "west"):``.
* E3 "no record holds": ``if pre is None or side != pre:`` made ``if pre is
  not None and side != pre:``, so a target with no record goes on to the
  hold. (E3-literal, ``pre is not None or side != pre``, is GREEN on the
  no-record night, as the subsumption says, and RED on holds, whose record
  it now lets through; observed.)
* E4 "changed side ignored": ``side != pre`` made ``side == pre``.
* E5 "the hold not taken": ``if schedule.flip_can_be_skipped(...)`` made
  ``if not schedule.flip_can_be_skipped(...)``.
"""
from __future__ import annotations

import pytest

from _group_harness import (T0, Night, close_night_hub, group_store,  # noqa: F401
                            night_hub)
from astrodeck.config import SafetyConfig
from astrodeck.devices.base import PierSide
from test_group_rotation import _golden_as_recorded

GOLDEN = "NGC 7331"
RETRY = "nothing flipped this time either. "
HOLD_OPENED = "a meridian flip is owed and the mount is still on the"

#: Each case's sentence, as `_flip_owed_words` words it, and what the
#: sentence says the next frame gets.
SENTENCES = {
    "hold-off": ("The flip-owed hold is off (safety.flip_owed_hold_min is "
                 "0), so nothing stops the next frame being exposed on this "
                 "side past the meridian", "through"),
    "unreadable": ("The mount's side cannot be read, so the flip-owed "
                   "invariant cannot hold the frame", "through"),
    "no-record": ("No pier side was seen for this target before the "
                  "meridian, so the flip-owed invariant has nothing to "
                  "compare against and will not hold the frame", "through"),
    "other-side": ("The mount is not on the east side it was seen on before "
                   "the meridian, so the flip-owed invariant lets the frame "
                   "through", "through"),
    "holds": ("The flip-owed invariant now holds the frame: nothing is "
              "exposed past the meridian while the mount stays on the west "
              "side, for up to 20 min", "held"),
}

#: The golden target crosses at about 10770.6 s from ``T0``; the no-record
#: night starts a minute past that.
PAST_THE_CROSSING_T0 = T0 + 10830.0
#: When the other-side night's mount starts reading west.
SIDE_CHANGES_AT = T0 + 5000.0


async def _night(case: str, group_store, monkeypatch) -> Night:
    """Run ``case``'s staged night to its end and return it."""
    if case == "hold-off":
        group_store.set_safety(SafetyConfig(enabled=False,
                                            flip_owed_hold_min=0.0))
    hub, popped = await night_hub(monkeypatch)
    try:
        t0 = PAST_THE_CROSSING_T0 if case == "no-record" else T0
        night = Night(hub, monkeypatch, t0=t0)
        tel = hub.devices["telescope"]

        async def side():
            if case == "unreadable":
                return PierSide.UNKNOWN
            if case == "other-side" and night.clock.t < SIDE_CHANGES_AT:
                return PierSide.EAST
            return PierSide.WEST

        monkeypatch.setattr(tel, "pier_side", side)
        if case == "unreadable":
            async def hub_reads_west():
                return "west"

            monkeypatch.setattr(hub, "pier_side_now", hub_reads_west)
        try:
            night.done = await night.run(_golden_as_recorded(), wall_s=120.0)
        finally:
            await night.close()
    finally:
        await close_night_hub(hub, popped)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    return night


def _retry_and_next(night: Night) -> tuple[str, str | None]:
    """The sentence the no-op retry's warning carried, and what the frame
    loop did next, in the trace's order: "through" when the next thing is
    an exposure, "held" when it is the flip-owed hold opening."""
    retries = [i for i, e in enumerate(night.trace)
               if e[1] == "log" and RETRY in e[3]]
    assert len(retries) == 1, (
        f"premise: one retry flipped nothing: "
        f"{[night.trace[i][3] for i in retries]}")
    i = retries[0]
    level, line = night.trace[i][2], night.trace[i][3]
    assert level == "warning", (level, line)
    sentence = line.split(RETRY, 1)[1]
    for entry in night.trace[i + 1:]:
        if entry[1] == "capture":
            return sentence, "through"
        if entry[1] == "log" and HOLD_OPENED in entry[3]:
            return sentence, "held"
    return sentence, None


@pytest.mark.parametrize("case", list(SENTENCES))
async def test_the_retry_sentence_is_what_the_invariant_does(
        group_store, monkeypatch, case):
    """The no-op retry's warning carries ``case``'s sentence, and the next
    frame then gets exactly what the sentence says: exposed for the four
    that say the frame is not held, and the flip-owed hold for "holds",
    which then holds to its end with nothing exposed.

    E1 "hold off ignored": RED on hold-off (observed), which opened a hold
    of 0 min and ended it at once; other-side and holds red too, on their
    premise, since the early return also stopped the first sighting being
    recorded, and unreadable and no-record green:
        AssertionError: hold-off: the retry said the next frame is
        'through' ('The flip-owed hold is off (safety.flip_owed_hold_min is
        0), so nothing stops the next frame being exposed on this side past
        the meridian'), and the frame loop did 'held'
        assert 'held' == 'through'
        AssertionError: premise: the holds night's retry was worded from
        another case: 'No pier side was seen for this target before the
        meridian, so the flip-owed invariant has nothing to compare against
        and will not hold the frame'
    E2 "unreadable read as a side": RED on unreadable (observed; the first
    sighting recorded "unknown", which the side past the meridian then
    matched), other-side and holds red on their premise as under E1, and
    hold-off and no-record green:
        AssertionError: unreadable: the retry said the next frame is
        'through' ("The mount's side cannot be read, so the flip-owed
        invariant cannot hold the frame"), and the frame loop did 'held'
        assert 'held' == 'through'
    E3 "no record holds": RED on no-record, the other four green
    (observed):
        AssertionError: no-record: the retry said the next frame is
        'through' ('No pier side was seen for this target before the
        meridian, so the flip-owed invariant has nothing to compare against
        and will not hold the frame'), and the frame loop did 'held'
        assert 'held' == 'through'
    E3-literal: GREEN on no-record, RED on holds (observed):
        AssertionError: holds: the retry said the next frame is 'held'
        ('The flip-owed invariant now holds the frame: nothing is exposed
        past the meridian while the mount stays on the west side, for up to
        20 min'), and the frame loop did 'through'
        assert 'through' == 'held'
    E4 "changed side ignored": RED on other-side and on holds, the other
    three green (observed):
        AssertionError: other-side: the retry said the next frame is
        'through' ('The mount is not on the east side it was seen on before
        the meridian, so the flip-owed invariant lets the frame through'),
        and the frame loop did 'held'
        assert 'held' == 'through'
    E5 "the hold not taken": RED on holds, the other four green (observed):
        AssertionError: holds: the retry said the next frame is 'held'
        ('The flip-owed invariant now holds the frame: nothing is exposed
        past the meridian while the mount stays on the west side, for up to
        20 min'), and the frame loop did 'through'
        assert 'through' == 'held'
    """
    want_sentence, says = SENTENCES[case]
    night = await _night(case, group_store, monkeypatch)
    sentence, did = _retry_and_next(night)
    assert sentence == want_sentence, (
        f"premise: the {case} night's retry was worded from another case: "
        f"{sentence!r}")
    assert did == says, (
        f"{case}: the retry said the next frame is {says!r} "
        f"({sentence!r}), and the frame loop did {did!r}")
    if says == "held":
        # Held to its end: nothing exposed until the hold's StopTarget.
        opened = next(i for i, e in enumerate(night.trace)
                      if e[1] == "log" and HOLD_OPENED in e[3])
        ended = next(i for i, e in enumerate(night.trace)
                     if e[1] == "log"
                     and "a meridian flip has been owed for 20 min" in e[3])
        exposed = [e[0] for e in night.trace[opened:ended]
                   if e[1] == "capture"]
        assert exposed == [], f"{case}: exposed while held at {exposed}"
    else:
        assert not night.said(HOLD_OPENED), night.said(HOLD_OPENED)
