# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1213: a frame never lands in a stack that Stop or Reset emptied while the
frame was on its way in.

``SessionStacker.add`` checked ``enabled`` before taking its lock, so a caller
that passed the check could wait on the lock while ``stop`` held it and then
add a frame to the stack the operator had just switched off (main's CI, after
wave 3: ``test_a_stack_switched_off_mid_pass_does_not_come_back``). The backfill
worker checked the generation before its slow FITS read, so a Reset pressed
during the read let the old frame into the emptied stack.

Both interleavings are forced here, not raced: the hook runs Stop (or Reset)
at exactly the point the old code was exposed.

MUTANT "no re-check under the lock" (the ``if not self.enabled or (generation
...)`` lines inside ``add``'s lock deleted): RED, both tests find a frame in
the emptied stack.
"""
from __future__ import annotations

import sys

import astrodeck.imaging.sessionstack as ss
import astrodeck.imaging.stackbackfill as sb
from astrodeck.imaging.sessionstack import SessionStacker
from astrodeck.imaging.stackbackfill import plan_backfill, run_backfill

from test_session_stack_backfill import field, make_session


def test_a_stop_between_adds_switch_check_and_its_lock_lands_nothing(
        monkeypatch):
    """``frame_key`` is the one call between ``add``'s first ``enabled`` check
    and its lock; Stop runs there, as it would if it took the lock first."""
    s = SessionStacker()
    s.start()
    real = ss.frame_key

    def stop_inside_add(key):
        if sys._getframe(1).f_code.co_name == "add":
            s.stop()
        return real(key)

    monkeypatch.setattr(ss, "frame_key", stop_inside_add)
    landed = s.add(field(), "R", 60.0, target="M42", session="night-1",
                   key="Light_1.fits")
    assert landed is None, "a frame landed in a stack switched off under it"
    assert s.status()["frames"] == 0
    assert s.enabled is False


def test_a_reset_during_the_backfill_read_keeps_the_old_frame_out(
        tmp_path, monkeypatch):
    """The worker reads a frame from disk, the operator presses Reset, and the
    frame it read must not refill the emptied stack; the pass says stopped."""
    session, _ = make_session(tmp_path, [("R",)] * 3)
    s = SessionStacker()
    s.start()
    items = plan_backfill(session)
    real = sb.read_backfill_frame
    calls = {"n": 0}

    def reset_during_read(item):
        out = real(item)
        calls["n"] += 1
        if calls["n"] == 1:
            s.reset(s.target, s.session)       # the operator's Reset
        return out

    monkeypatch.setattr(sb, "read_backfill_frame", reset_during_read)
    p = run_backfill(s, items)
    assert s.status()["frames"] == 0, (
        "a frame read before the Reset refilled the emptied stack")
    assert p.error == "stopped"
    assert p.added == 0
