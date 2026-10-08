# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A RESUMED session must be armed too, or a multi-night run can never resume.

MEASURED ON THE RIG, 2026-08-11. I deployed 0.2.73 into a live plan
(164/180 frames) specifically to watch auto-resume work. It did not run. The
sequence sat idle for 25 minutes and — the part that made it hard to see — the
log said NOTHING after the boot line. No veto, no retry, no alert.

The chain:

  * ``engine.start`` armed a session only in the ``session is None`` branch, so
    a FRESH run was armed and a RESUMED one inherited whatever flag it carried.
  * The singleton rule disarms every other session when one is armed. So on
    2026-08-10 a new session started, disarmed 810d46ee, and nothing ever put
    the flag back.
  * On 2026-08-11 the run RESUMED 810d46ee (a multi-night session) and shot 165
    frames with ``auto_resume`` still False.
  * The restart marked it dormant. ``session_store.armed()`` requires
    ``status == "dormant" and auto_resume``, so it returned None, and
    ``resume_arm.tick`` returns silently on None.

The feature was absent, quietly, on exactly the path it exists for — the second
and later nights of a multi-night run, which is how this rig is actually used.

The invariant these tests pin: **the ACTIVE session is the armed one**, however
it became active.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.sequence.session import Session, session_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolate the store by moving CAPTURE_DIR — `_sessions_dir()` resolves
    `hub.CAPTURE_DIR / "sessions"` lazily on every call, so this is the seam.
    Patching an attribute on the store itself does nothing and quietly leaves
    the test reading the real machine's sessions, which is how the first
    version of this fixture produced a confusing failure."""
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    assert session_store.load_all() == [], "the store is not isolated"
    return session_store


def _plan():
    from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
    return SequencePlan(
        name="NGC 6946 Fireworks - LRGB+Ha cycle", guide=False,
        dither_every=0, autofocus_every=0, meridian_flip=False,
        targets=[Target(name="NGC 6946", ra_hours=20.34, dec_deg=60.15,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])


class TestArmingIsAboutTheActiveSession:
    def test_a_fresh_run_is_armed(self, store):
        from astrodeck.sequence.engine import SequenceEngine
        import inspect
        src = inspect.getsource(SequenceEngine.start)
        assert "session.auto_resume = True" in src, \
            "nothing arms the session at all"

    def test_arming_is_NOT_inside_the_fresh_only_branch(self):
        """THE regression. If the assignment moves back under
        `if session is None:` a resumed session goes unarmed again and the
        silence returns."""
        import inspect
        from astrodeck.sequence.engine import SequenceEngine
        src = inspect.getsource(SequenceEngine.start)
        arm = src.index("session.auto_resume = True")
        else_branch = src.index('session.status = "active"')
        assert arm > else_branch, (
            "auto_resume is set before the resume branch runs — it is back "
            "inside the fresh-run-only path")

    def test_the_singleton_still_holds(self):
        """DELIBERATE PIN CHANGE (#837, wave 17 integration). This asserted
        ``other.auto_resume = False`` in ``SequenceEngine.start``'s OWN source,
        back when ``start`` ran its own copy of the singleton loop beside the
        PATCH route's and the queue promotion's. The loop is written once now
        (``SequenceEngine._arm_exclusively``) and all three call it, so the pin
        follows the loop: ``start`` must still call the shared loop, and the
        shared loop must still disarm every other session. The behaviour itself
        (a start returns and logs the session it disarmed) is pinned by
        ``test_w4_disarm_visible.py``, which did not change.

        RED under mutant "start no longer arms exclusively" (``disarmed =
        self._arm_exclusively(session)`` removed from ``start``), observed:

            AssertionError: start no longer calls the shared singleton loop
        """
        import inspect
        from astrodeck.sequence.engine import SequenceEngine
        src = inspect.getsource(SequenceEngine.start)
        assert "self._arm_exclusively(session)" in src, \
            "start no longer calls the shared singleton loop — two armed sessions would race"
        loop = inspect.getsource(SequenceEngine._arm_exclusively)
        assert "other.auto_resume = False" in loop, \
            "arming no longer disarms the others — two armed sessions would race"


class TestTheRigSequenceThatFailed:
    """Replay 2026-08-10/11 against the store: fresh A, fresh B (disarms A),
    resume A, restart. A must be armed at the end."""

    def test_a_superseded_session_is_re_armed_when_resumed(self, store):
        a = Session(name="night one", created_ts=time.time(), status="active",
                    plan=_plan(), auto_resume=True)
        store.save(a)
        b = Session(name="night two", created_ts=time.time(), status="active",
                    plan=_plan(), auto_resume=True)
        # the singleton rule, as engine.start applies it
        for other in store.load_all():
            if other.id != b.id and other.auto_resume:
                other.auto_resume = False
                store.save(other)
        store.save(b)

        reloaded_a = next(s for s in store.load_all() if s.id == a.id)
        assert reloaded_a.auto_resume is False, "fixture did not reproduce the disarm"

        # Now RESUME a — the fixed contract says becoming active arms it.
        reloaded_a.status = "active"
        reloaded_a.auto_resume = True
        for other in store.load_all():
            if other.id != reloaded_a.id and other.auto_resume:
                other.auto_resume = False
                store.save(other)
        store.save(reloaded_a)

        # ...and a restart makes it dormant.
        again = next(s for s in store.load_all() if s.id == a.id)
        again.status = "dormant"
        store.save(again)

        armed = store.armed()
        assert armed is not None, \
            "the resumed session is not armed — resume_arm.tick returns silently"
        assert armed.id == a.id

    def test_armed_requires_both_dormant_and_auto_resume(self, store):
        """Why the failure was invisible: armed() ANDs two fields, and the one
        that was wrong is not the one anybody looks at after a crash."""
        s = Session(name="x", created_ts=time.time(), status="dormant",
                    plan=_plan(), auto_resume=False)
        store.save(s)
        assert store.armed() is None
        s.auto_resume = True
        store.save(s)
        assert store.armed() is not None


class TestTheSilenceItself:
    def test_tick_says_something_when_nothing_is_armed(self):
        """25 minutes of nothing is not a diagnosis. Whatever the tick decides,
        a dormant session that will NOT be resumed has to be visible somewhere —
        otherwise the next person also spends the night reading session JSON by
        hand.

        This asserts the shape rather than the wording: `armed() is None` must
        not be a bare early return."""
        import inspect
        from astrodeck.sequence.resume_arm import ResumeArm
        src = inspect.getsource(ResumeArm.tick)
        # The region between `armed is None` and the return that ends it.
        start = src.index("if armed is None:")
        region = src[start:start + 1800]
        assert "bus.log" in region, (
            "tick returns silently when nothing is armed — a dormant, unarmed "
            "session is invisible, which is how 25 minutes of idle went "
            "unexplained on 2026-08-11")
