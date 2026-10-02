# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#127: a mount that cannot flip before the meridian should only have to
demonstrate that once.

THE LEAD is an optimisation for a mount that stops tracking BEFORE the meridian
(measured 7.6 min on NGC 7129 and 4.7 min on NGC 6946, both answering `:Te#`
with 0): flip early rather than arrive late. But what the lead triggers is a
GoTo, and a GoTo picks the pier side by hour angle - so while the target is
still east it picks the SAME side, and on this mount the early attempt cannot
succeed by construction. The engine finds that out by spending a full flip:
stop guiding, re-slew, re-centre, restart guiding, arrive where it started.

MEASURED, from the durable log on two consecutive nights: the whole crossing,
first attempt to completed flip, took 18.2 and 19.6 minutes and held 1 and 2
light frames against a cadence that would have fitted 7.5 and 7.0. Five or six
frames per crossing.

`_flip_no_op` already records "this target's early flip changed nothing" and
drops the lead for the retry (#25). It is cleared at run start and keyed per
target, so a three-target night pays three times and tomorrow pays again. The
fact being learned is a property of the MOUNT.

WHY IT IS NOT PERSISTED, since that was the first attempt and it was wrong:
the engine is a module-level singleton (`api/app.py:168`) that lives as long as
the server, which on this rig is days - far longer than between mount changes -
so a disk store buys almost nothing over an in-memory one. Against that, a
night that behaves differently because of a JSON file written by a previous
night is a night nobody can explain from the code in front of them. It also
broke 27 existing tests the moment it was tried, because a global store means
the first test to learn the trait teaches every test that runs after it.

MUTATIONS RUN, against this file and `test_the_flip_stops_paying_for_itself`
together (41 cases):

  M1, delete the `_mount_flips_early() is False` arm of `_flip_lead_s`, which
  is the defect exactly as it stands today. 2 failed: the second target and the
  fresh run - the two places the cost recurs.

  M2, learn from an attempt taken at the crossing as well. 1 failed: the
  crossing case, which is the one that would put the useless attempt back by
  recording the ordinary flip as evidence about early ones.

  M3, drop the readable-sides guard. 1 failed: the unreadable case.

  M4, key the trait on a constant instead of the mount name. 7 failed,
  including both directions of the learned answer - a shared bucket is not a
  fact about any mount.

  M5, always record False. 2 failed: the control, and the True direction of
  the parametrised case. This is the mutation a test suite without a control
  would miss, because False is the right answer on this rig.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import schedule

# rootdir-relative. `sim_hub` comes with `_flip_engine` deliberately: the
# harness pairs them (a real site, a sim rig, and the engine built on it), and
# a second copy of the fixture here would be a second thing to keep in step.
from test_the_flip_stops_paying_for_itself import (  # noqa: F401
    _flip_engine, sim_hub)


async def test_the_second_target_does_not_re_buy_the_lesson(sim_hub,
                                                            monkeypatch):
    """THE FIX. Target 1's early attempt is the price of learning; target 2
    starts with the lead already at zero and never makes the useless slew."""
    e, first, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    assert e._flip_lead_s(first) > 0, "premise: the first target pays the lead"

    await e._maybe_meridian_flip(first, next_exposure_s=180.0)
    slews_for_the_lesson = len(st["gotos"])
    assert slews_for_the_lesson >= 1, "premise: the lesson cost a real slew"

    # A DIFFERENT target, on the same mount, in the same run.
    second = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)[1]
    second.name = "T2"
    second.id = "another-target"
    assert e._flip_lead_s(second) == 0.0, (
        "the second target kept the 10 min lead, so it will spend another "
        "stop-guiding, re-slew and re-centre to arrive back where it started")


async def test_a_fresh_run_does_not_forget(sim_hub, monkeypatch):
    """`_flip_no_op` is cleared at run start, and that clearing is what made
    this cost recur. The mount fact must survive it."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert e._flip_no_op, "premise: the per-target memory was set"

    e._flip_no_op = set()                      # exactly what _start does
    assert e._flip_lead_s(t) == 0.0, (
        "clearing the per-target memory also forgot what the MOUNT showed")


# ------------------------------------------------------------ the control

async def test_a_mount_that_does_flip_early_keeps_its_lead(sim_hub,
                                                           monkeypatch):
    """THE HALF THAT MAKES THIS SAFE. The lead exists to protect a mount that
    stops tracking before the meridian; taking it away from a mount that CAN
    flip early hands it a flip it cannot make in time. So the trait is learned,
    never assumed, and a successful early flip teaches the opposite."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
    lead_before = e._flip_lead_s(t)
    assert lead_before > 0

    await e._maybe_meridian_flip(t, next_exposure_s=180.0)

    assert e._mount_flips_early() is True
    other = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)[1]
    other.id = "another-target"
    assert e._flip_lead_s(other) == lead_before, (
        "a mount that flipped early lost the lead that makes early flips work")


async def test_nothing_is_learned_before_an_attempt(sim_hub, monkeypatch):
    """None, not False. An unknown mount keeps its lead and pays once."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    assert e._mount_flips_early() is None
    assert e._flip_lead_s(t) > 0


async def test_an_attempt_at_the_crossing_teaches_nothing_about_early_flips(
        sim_hub, monkeypatch):
    """A flip taken with the lead already at zero is the ORDINARY flip. That
    it worked says nothing about whether an EARLY one would have, and writing
    it down as "this mount flips early" would restore the useless attempt on
    every later target.

    RE-PINNED FOR H4 (#489). The retry is now put AT the crossing: the
    target's RA is moved so it transited 20 s ago before the retry runs.
    Before H4 the engine read "at the crossing" from the retry's lead being
    zero, so this case could leave the target where the harness armed it,
    9 min before transit, and let the stubbed hold stand in for the wait.
    Since #489 the engine reads how far before transit the goto itself is
    made, which is the fact that decides whether a flip says anything about
    flipping early, and a zero-lead goto 9 min before transit is an early
    attempt: left as it was, this case taught "flips early: True" and failed.

    RED under M2 as re-run by the H4 integration ("learn from an attempt
    taken at the crossing as well": engine.py's
    ``if (before_transit_s > MERIDIAN_SIDE_MARGIN_S`` made ``if (True``).
    Observed:

        AssertionError: a flip that succeeded AT THE CROSSING was recorded
        as evidence that this mount flips EARLY, which puts the useless
        attempt back
        assert True is False
         +  where True = _mount_flips_early()
    """
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)   # learns False
    assert e._mount_flips_early() is False
    assert e._flip_lead_s(t) == 0.0, "premise: the retry has no lead"

    # The retry is made past the meridian, 20 s after transit, the crossing
    # the stubbed hold would have waited for. This is the harness's own
    # arming rule (`_flip_engine`: RA = LST + ttf) with ttf at -20 s.
    t.ra_hours = (schedule.lst_hours(sim_hub.site["longitude"])
                  - 20.0 / 3600.0) % 24.0
    st["flips"] = True                       # the retry, at the crossing, works
    await e._maybe_meridian_flip(t, next_exposure_s=100000.0)

    assert e._mount_flips_early() is False, (
        "a flip that succeeded AT THE CROSSING was recorded as evidence that "
        "this mount flips EARLY, which puts the useless attempt back")


async def test_an_unreadable_pier_side_is_not_written_down_as_a_fact(
        sim_hub, monkeypatch):
    """A failure to MEASURE reads as FLIPPED, which is the right conservative
    answer for this crossing and exactly the wrong thing to keep as a
    permanent fact about the hardware."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)

    async def _blind():
        raise RuntimeError("pier side unreadable")

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _blind)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)

    assert e._mount_flips_early() is None, (
        "two failed reads were recorded as a measurement of this mount")


# ----------------------------------------------------- it is about the mount

async def test_a_different_mount_learns_for_itself(sim_hub, monkeypatch):
    """Keyed on the mount's name, so swapping hardware - or activating a
    profile with a different mount - re-learns instead of inheriting."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert e._mount_flips_early() is False

    sim_hub.devices["telescope"].name = "Some Other Mount"
    assert e._mount_flips_early() is None, (
        "a fact measured on one mount was applied to another")
    assert e._flip_lead_s(t) == 0.0, (
        "...but this target's own _flip_no_op memory still stands on its own")


async def test_an_unnamed_mount_learns_nothing_rather_than_guessing(
        sim_hub, monkeypatch):
    """No name, no key. The alternative - one shared bucket for every unnamed
    mount - is how a fact about one rig reaches another."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False)
    sim_hub.devices["telescope"].name = ""
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert e._mount_flips_early() is None


def test_nothing_is_written_to_disk(sim_hub, tmp_path):
    """The first version of this feature persisted the trait under CONFIG_DIR
    and cost 27 test failures through cross-test contamination. Pinned so it
    cannot come back by accident."""
    from astrodeck import config as cfgmod
    before = sorted(p.name for p in cfgmod.CONFIG_DIR.glob("*.json")) \
        if cfgmod.CONFIG_DIR.exists() else []
    assert "mount_traits.json" not in before
    assert not hasattr(cfgmod, "save_mount_trait"), (
        "the learned-trait store is back; see this module's docstring for why "
        "the engine's own lifetime is the right scope")


@pytest.mark.parametrize("flips", [True, False])
async def test_the_learned_answer_is_what_the_attempt_showed(sim_hub,
                                                             monkeypatch,
                                                             flips):
    """Both directions, so the recording cannot be a constant that happens to
    match the common case."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=flips)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert e._mount_flips_early() is flips
